"""Real A-loop queue lifecycle, with local synthetic sources and no providers."""
import asyncio
import copy
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
from tests.research_report_fixture import ReportFixture,QUESTION
from tcm.research_jobs import JobStore,ResearchWorker,JobConflict,JobBusy
from tcm.research_plan_run import execute,restore
from tcm.research_plan_jobs import get,journal_path,cleanup
from tcm.research_run_local import run_local_actions


class PlanJobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture=ReportFixture();self.f=fixture.__enter__();self.addCleanup(fixture.__exit__,None,None,None)
        self.path=self.f.root/'runs.sqlite3';self.queue=JobStore(self.f.root/'jobs.sqlite3')
        env=patch.dict(os.environ,{'ENABLE_RESEARCH_PLAN_JOBS':'1','ENABLE_RESEARCH_TASKS':'1',
            'RESEARCH_JOB_DB':str(self.queue.path),'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':''})
        env.start();self.addCleanup(env.stop)

    async def submit(self,question=QUESTION,key='request-one',owner='alice'):
        value,focus=await execute({'question':question,'request_key':key},None,owner=owner,path=self.path)
        self.assertEqual(value['status'],'queued',value)
        return value['run_id'],focus

    def worker(self):
        worker=ResearchWorker(lambda:self.queue,lambda:None,run_path=lambda:self.path)
        self.addAsyncCleanup(worker.stop);return worker

    async def wait(self,identifier,statuses=('complete','partial','failed','cancelled','interrupted')):
        for _ in range(300):
            doc=self.queue.get('alice',identifier)
            if doc['status'] in statuses:return doc
            await asyncio.sleep(.02)
        self.fail('Task did not reach expected state')

    async def test_detached_plan_complete_restore_and_private_evidence(self):
        identifier,focus=await self.submit();self.assertEqual(restore(focus,owner='alice',path=self.path)['status'],'queued')
        self.worker().start();doc=await self.wait(identifier)
        self.assertEqual(doc['status'],'complete',doc)
        self.assertEqual(doc['result']['run']['tool_calls'],6)
        self.assertEqual(doc['result']['run']['action_calls'],1)
        shown=restore(focus,owner='alice',path=self.path)
        self.assertEqual(shown['status'],'ok');self.assertEqual(shown['blocks'][0]['shared_total'],23)
        with self.assertRaises(KeyError):get('bob',identifier,self.path)
        self.assertNotIn('P00000',json.dumps(focus))
        self.assertNotIn('SYNTHETIC_Alpha',json.dumps(doc['payload']))

    async def test_idempotency_and_busy_do_not_start_extra_queries(self):
        identifier,_=await self.submit()
        duplicate,_=await execute({'question':QUESTION,'request_key':'request-one'},None,owner='alice',path=self.path)
        self.assertEqual(duplicate['run_id'],identifier)
        self.assertEqual(len(self.queue.list('alice')),1)
        blocked,_=await execute({'question':QUESTION,'request_key':'different-key'},None,owner='alice',path=self.path)
        self.assertEqual(blocked['status'],'busy')
        with self.assertRaises(JobConflict):self.queue.enqueue('alice','changed','request-one')
        self.assertFalse(journal_path(self.path).exists())

    async def test_queued_cancel_and_running_cancel_stop_child_branches(self):
        identifier,_=await self.submit();self.queue.cancel('alice',identifier)
        self.assertIsNone(self.queue.claim('never-runs'))
        identifier,_=await self.submit(key='running-cancel')
        started=asyncio.Event();stopped=asyncio.Event()
        async def branch(*args,**kwargs):
            started.set()
            try:await asyncio.Future()
            finally:stopped.set()
        with patch('tcm.cross_source_report.local_branch',side_effect=branch):
            self.worker().start();await asyncio.wait_for(started.wait(),4)
            self.queue.cancel('alice',identifier);doc=await self.wait(identifier)
            await asyncio.wait_for(stopped.wait(),2)
        self.assertEqual(doc['status'],'cancelled');self.assertIsNone(doc['result'])
        with self.assertRaises(JobConflict):self.queue.resume('alice',identifier,expected_revision=doc['revision'])

    async def test_saved_observation_survives_shutdown_and_resume_without_requery(self):
        q='Alpha的靶点画像，Beta的靶点画像';identifier,_=await self.submit(q)
        reached=asyncio.Event();calls=[]
        from tcm.research_run_local import LocalAnalysisTools
        original=LocalAnalysisTools.__call__
        async def counted(backend,name,args):calls.append((name,args['name']));return await original(backend,name,args)
        async def pause(*args,on_state,**kwargs):
            async def persist(snapshot):
                await on_state(snapshot)
                if snapshot.get('last_node')=='execute' and snapshot['tool_calls']==1:
                    reached.set();await asyncio.Future()
            return await run_local_actions(*args,on_state=persist,**kwargs)
        with patch.object(LocalAnalysisTools,'__call__',counted):
            with patch('tcm.research_run_local.run_local_actions',side_effect=pause):
                first=self.worker();first.start();await asyncio.wait_for(reached.wait(),5);await first.stop()
            stopped=self.queue.get('alice',identifier);self.assertEqual(stopped['status'],'interrupted')
            with self.assertRaises(JobConflict):self.queue.resume('alice',identifier,expected_revision=stopped['revision']-1)
            self.queue.resume('alice',identifier,expected_revision=stopped['revision']);self.worker().start();doc=await self.wait(identifier)
        self.assertEqual(doc['status'],'complete',doc)
        self.assertEqual(len(calls),2);self.assertEqual(len(set(name for _,name in calls)),2)
        self.assertEqual(doc['result']['run']['tool_calls'],2)

    async def test_uncertain_inner_dispatch_never_replays(self):
        identifier,_=await self.submit();started=asyncio.Event();calls=[]
        async def blocked(*args,**kwargs):
            calls.append(args[0]);started.set();await asyncio.Future()
        with patch('tcm.cross_source_report.local_branch',side_effect=blocked):
            first=self.worker();first.start();await asyncio.wait_for(started.wait(),4);await first.stop()
            doc=self.queue.get('alice',identifier);before=len(calls)
            self.queue.resume('alice',identifier,expected_revision=doc['revision']);self.worker().start();doc=await self.wait(identifier)
        self.assertEqual(doc['status'],'partial',doc)
        self.assertEqual(doc['result']['run']['reason'],'uncertain_tool_dispatch')
        self.assertEqual(len(calls),before)

    async def test_source_or_code_changed_jobs_cannot_execute_or_resume(self):
        identifier,focus=await self.submit();path=self.f.pharma/'manifest.json';path.write_text(path.read_text()+' ')
        self.assertEqual(restore(focus,owner='alice',path=self.path)['status'],'unavailable')
        with patch('tcm.research_run_local.run_local_actions',side_effect=AssertionError('Changed source executed')):
            self.worker().start();doc=await self.wait(identifier)
        self.assertEqual(doc['status'],'failed')
        with self.assertRaises(JobConflict):get('alice',identifier,self.path)
        # A queued job is bound before its first journal is created.
        identifier,_=await self.submit(key='new-source')
        with patch('tcm.research_plan_jobs.code_revision',return_value='other-code'):
            worker=self.worker();worker.start();doc=await self.wait(identifier)
        self.assertEqual(doc['status'],'failed')

    async def test_cancellation_wins_at_final_commit_and_cleanup_removes_private_state(self):
        identifier,_=await self.submit();original=self.queue.save
        def save(owner,doc,worker,**kwargs):
            if kwargs.get('status')=='complete':self.queue.cancel(owner,doc['id'])
            return original(owner,doc,worker,**kwargs)
        with patch.object(self.queue,'save',side_effect=save):
            self.worker().start();doc=await self.wait(identifier)
        self.assertEqual(doc['status'],'cancelled');self.assertIsNone(doc['result'])
        cleanup('alice',identifier,self.path,self.queue)
        with self.assertRaises(KeyError):self.queue.get('alice',identifier)
        with sqlite3.connect(journal_path(self.path)) as db:self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0],0)
        with sqlite3.connect(self.path.with_name('evidence.sqlite3')) as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],0)

    async def test_two_workers_and_resume_before_initial_journal(self):
        identifier,_=await self.submit();_,doc=self.queue.claim('stale')
        doc=self.queue.save('alice',doc,'stale',status='interrupted')
        self.queue.resume('alice',identifier,expected_revision=doc['revision'])
        from tcm.research_run_local import LocalAnalysisTools
        calls=[];original=LocalAnalysisTools.invoke_metered
        async def counted(backend,*args):calls.append(args[0]);return await original(backend,*args)
        with patch.object(LocalAnalysisTools,'invoke_metered',counted):
            self.worker().start();self.worker().start();doc=await self.wait(identifier)
        self.assertEqual(doc['status'],'complete',doc);self.assertEqual(calls,['pair_evidence_report'])

    async def test_missing_fields_stay_partial_in_queue(self):
        identifier,_=await self.submit(QUESTION.replace('功效和用量','功效和用量和贮藏'))
        self.worker().start();doc=await self.wait(identifier)
        self.assertEqual(doc['status'],'partial')
        self.assertEqual(doc['result']['display']['blocks'][0]['missing'],['Alpha：贮藏','Beta：贮藏'])

    async def test_backup_checks_queue_evidence_and_preserves_plan_journal(self):
        from tests.test_runtime_backup import RuntimeBackupTests
        from tcm.runtime_backup import create_bundle,restore_bundle,RuntimeBackupError
        helper=RuntimeBackupTests();helper.setUp();self.addCleanup(helper.doCleanups)
        identifier,_=await self.submit();self.worker().start();await self.wait(identifier)
        files={**helper.files,'jobs':self.queue.path,'plans':journal_path(self.path)}
        with self.assertRaisesRegex(RuntimeBackupError,'referenced_evidence_required'):
            create_bundle(files,helper.bundle,workers_stopped=True)
        files['evidence']=self.path.with_name('evidence.sqlite3')
        create_bundle(files,helper.bundle,workers_stopped=True)
        target=helper.root/'restored-plan';restore_bundle(helper.bundle,target)
        restored=JobStore(target/'jobs.sqlite3').get('alice',identifier)
        self.assertEqual(restored['result']['run']['tool_calls'],6)
        with sqlite3.connect(target/'plans.sqlite3') as db:self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0],1)

    async def test_interrupted_backup_requires_journal_and_deletion_respects_execution_lock(self):
        from tests.test_runtime_backup import RuntimeBackupTests
        from tcm.runtime_backup import create_bundle,RuntimeBackupError
        from tcm.research_run_durable import RunJournal,RunConflict
        helper=RuntimeBackupTests();helper.setUp();self.addCleanup(helper.doCleanups)
        identifier,_=await self.submit();_,doc=self.queue.claim('old')
        self.queue.save('alice',doc,'old',status='interrupted')
        with self.assertRaisesRegex(RuntimeBackupError,'interrupted_plan_journal_required'):
            create_bundle({**helper.files,'jobs':self.queue.path},helper.bundle,workers_stopped=True)
        with RunJournal(journal_path(self.path),'alice',identifier,'test').locked():
            with self.assertRaises(RunConflict):cleanup('alice',identifier,self.path,self.queue)
        self.assertEqual(self.queue.get('alice',identifier)['status'],'interrupted')
