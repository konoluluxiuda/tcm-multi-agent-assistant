"""Durable pair graph recovery at leaf and dependency boundaries, no providers."""
import asyncio
from collections import Counter
import json
import unittest
from langgraph.checkpoint.memory import MemorySaver
from tcm.pair_research_report import run_pair_report
from tests.test_pair_research_report import report, targets


class PairCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_leaf_only_replays_unfinished_read(self):
        saver=MemorySaver();calls=Counter();events=[];waiting=asyncio.Event()
        payload={'kind':'pair_report','herbs':['a','b'],'question':'功能'}
        async def branch(kind,herb,question):
            calls[herb+'_'+kind]+=1
            if herb=='b' and kind=='structured' and calls['b_structured']==1:
                waiting.set();await asyncio.Event().wait()
            return report(herb)[kind]
        async def target(kind,herb,question):
            calls['target']+=1;return targets(json.loads(question))
        async def persist(snapshot):events.append(snapshot)
        async def run():return await run_pair_report(payload,checkpointer=saver,thread_id='owner-job',branch_backend=branch,target_backend=target,on_state=persist)
        task=asyncio.create_task(run());await asyncio.wait_for(waiting.wait(),2)
        # Wait for all other leaves to reach the checkpoint write store before interrupting.
        for _ in range(100):
            saved=await saver.aget_tuple({'configurable':{'thread_id':'owner-job'}})
            channels={w[1] for w in (saved.pending_writes if saved else [])}
            if {'left_text','left_structured','right_text'} <= channels:break
            await asyncio.sleep(.01)
        else:self.fail('Completed leaf writes missing')
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        result=await run()
        self.assertEqual(result['status'],'complete')
        self.assertEqual(calls,{'a_text':1,'a_structured':1,'b_text':1,'b_structured':2,'target':1})
        self.assertEqual(events[-1]['tool_calls'],5)
        self.assertEqual(await run(),result)  # terminal checkpoint is returned without new calls
        self.assertEqual(sum(calls.values()),6)

    async def test_target_interruption_retains_both_reports(self):
        saver=MemorySaver();calls=Counter();ready=asyncio.Event()
        async def branch(kind,herb,question):calls[herb+'_'+kind]+=1;return report(herb)[kind]
        async def target(kind,herb,question):
            calls['target']+=1
            if calls['target']==1:ready.set();await asyncio.Event().wait()
            return targets(json.loads(question))
        async def run():return await run_pair_report({'herbs':['a','b'],'question':'功能'},checkpointer=saver,thread_id='target',branch_backend=branch,target_backend=target)
        task=asyncio.create_task(run());await asyncio.wait_for(ready.wait(),2);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual((await run())['status'],'complete')
        self.assertEqual(calls,{'a_text':1,'a_structured':1,'b_text':1,'b_structured':1,'target':2})

    async def test_durable_and_original_outputs_match(self):
        async def branch(kind,herb,question):return report(herb)[kind]
        async def researcher(payload):return report(payload['herb'])
        async def target(kind,herb,question):return targets(json.loads(question))
        payload={'herbs':['a','b'],'question':'功能'}
        plain=await run_pair_report(payload,researcher=researcher,target_backend=target)
        durable=await run_pair_report(payload,checkpointer=MemorySaver(),thread_id='parity',branch_backend=branch,target_backend=target)
        # Durable uses the same single-report assembler, which adds explanatory metadata.
        for key in ('rows','targets','status','herbs','question'):
            self.assertEqual(plain[key],durable[key])

    async def test_durable_timeout_remains_partial(self):
        async def branch(kind,herb,question):
            if herb=='b' and kind=='structured':raise asyncio.TimeoutError()
            return report(herb)[kind]
        async def forbidden(*args):self.fail('Missing source must not query targets')
        result=await run_pair_report({'herbs':['a','b'],'question':'功能'},checkpointer=MemorySaver(),thread_id='timeout',branch_backend=branch,target_backend=forbidden)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['targets']['status'],'unresolved_sources')

    async def test_worker_routes_pair_to_checkpoint_and_refuses_disabled_replay(self):
        import os
        from pathlib import Path
        import tempfile
        from unittest.mock import patch, AsyncMock
        from tcm.research_jobs import JobStore, ResearchWorker
        with tempfile.TemporaryDirectory() as directory:
            store=JobStore(Path(directory)/'jobs.db')
            worker=ResearchWorker(lambda:store,lambda:'unused')
            payload={'kind':'pair_report','herbs':['a','b'],'question':'功能'}
            doc,_=store.enqueue('owner','compare','request-one',payload=payload)
            claimed=store.claim(worker.identity)
            with patch.dict(os.environ,{'RESEARCH_CHECKPOINT_DSN':'synthetic'}),patch('tcm.report_checkpoint.run_checkpoint_report',new_callable=AsyncMock,return_value={'status':'complete'}) as run:
                await worker.execute(store,*claimed)
                self.assertEqual(run.await_args.args[1]['recovery'],'checkpoint')
                self.assertEqual(store.get('owner',doc['id'])['status'],'complete')
            doc,_=store.enqueue('owner','compare','request-two',payload=payload)
            owner,doc=store.claim(worker.identity)
            doc=store.save(owner,{**doc,'recovery':'checkpoint'},worker.identity)
            with patch.dict(os.environ,{'RESEARCH_CHECKPOINT_DSN':''}),patch('tcm.pair_research_report.run_pair_report',new_callable=AsyncMock) as plain:
                await worker.execute(store,owner,doc)
                plain.assert_not_awaited()
                self.assertEqual(store.get(owner,doc['id'])['status'],'failed')
