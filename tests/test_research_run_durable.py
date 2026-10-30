"""Crash boundaries for the real bounded loop using deterministic SDK substitutes."""
import asyncio
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
from tcm.research_run import Limits
from tcm.research_run_demo import OfflineModel,synthetic_tool
from tcm.research_run_durable import run_durable,RunConflict,RunJournal


class Crash(BaseException):pass


class DurableTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'loop.sqlite3';self.calls=Counter()
        outer=self
        class Model(OfflineModel):
            async def ainvoke(self,messages):
                outer.calls['model']+=1;return await super().ainvoke(messages)
        self.model=Model()
        async def backend(name,args):
            self.calls['tool']+=1;return await synthetic_tool(name,args)
        self.backend=backend

    async def run_loop(self,**kw):
        options=dict(path=self.path,owner='alice',identifier='run-test-001',contract='synthetic-v1')
        options.update(kw)
        return await run_durable('synthetic',self.model,self.backend,**options)

    async def test_saved_action_and_observation_resume_without_repeating(self):
        for boundary in ('decide','execute'):
            with self.subTest(boundary=boundary):
                identifier='run-'+boundary
                async def stop(snapshot):
                    if snapshot['last_node']==boundary:raise Crash()
                before=self.calls.copy()
                with self.assertRaises(Crash):await self.run_loop(identifier=identifier,on_state=stop)
                result=await self.run_loop(identifier=identifier,resume=True)
                self.assertEqual(result['status'],'complete')
                self.assertEqual(self.calls-before,{'model':4,'tool':3})
                again=await self.run_loop(identifier=identifier,resume=True)
                self.assertEqual(result,again);self.assertEqual(self.calls-before,{'model':4,'tool':3})

    async def test_unknown_model_outcome_is_not_dispatched_twice(self):
        class Unknown(OfflineModel):
            async def ainvoke(inner,messages):self.calls['model']+=1;raise Crash()
        self.model=Unknown()
        with self.assertRaises(Crash):await self.run_loop()
        result=await self.run_loop(resume=True)
        self.assertEqual(result['reason'],'uncertain_model_dispatch')
        self.assertEqual((result['model_calls'],result['tool_calls']),(1,0))
        self.assertEqual(self.calls,{'model':1})

    async def test_unknown_tool_outcome_is_not_dispatched_twice(self):
        async def unknown(name,args):self.calls['tool']+=1;raise Crash()
        self.backend=unknown
        with self.assertRaises(Crash):await self.run_loop()
        result=await self.run_loop(resume=True)
        self.assertEqual(result['reason'],'uncertain_tool_dispatch')
        self.assertEqual((result['model_calls'],result['tool_calls']),(1,1))
        self.assertEqual(self.calls,{'model':1,'tool':1})

    async def test_budget_does_not_reset_and_binding_refuses_changes(self):
        limits=Limits(model_calls=1)
        async def stop(snapshot):
            if snapshot['last_node']=='execute':raise Crash()
        with self.assertRaises(Crash):await self.run_loop(limits=limits,on_state=stop)
        for options in ({'contract':'new-source'},{'limits':Limits(model_calls=2)},{'owner':'bob'}):
            with self.assertRaises((RunConflict,KeyError)):await self.run_loop(resume=True,**{'limits':limits,**options})
        result=await self.run_loop(resume=True,limits=limits)
        self.assertEqual(result['reason'],'model_budget');self.assertEqual(result['model_calls'],1)
        self.assertEqual(self.calls,{'model':1,'tool':1})

    async def test_expired_run_cannot_get_a_new_time_budget(self):
        from unittest.mock import patch
        async def stop(snapshot):raise Crash()
        with self.assertRaises(Crash):await self.run_loop(on_state=stop)
        import time
        with patch('tcm.research_run_durable.time.time',return_value=time.time()+200):
            result=await self.run_loop(resume=True)
        self.assertEqual(result['reason'],'time_budget');self.assertEqual(self.calls,{'model':1})

    async def test_concurrent_runner_and_wrong_owner_rejected(self):
        ready=asyncio.Event();release=asyncio.Event()
        class Blocking(OfflineModel):
            async def ainvoke(inner,m):ready.set();await release.wait();return await super().ainvoke(m)
        self.model=Blocking();task=asyncio.create_task(self.run_loop())
        await asyncio.wait_for(ready.wait(),2)
        try:
            with self.assertRaises(RunConflict):await self.run_loop(resume=True)
        finally:release.set()
        self.assertEqual((await task)['status'],'complete')
        with self.assertRaises(KeyError):await self.run_loop(owner='bob',resume=True)

    async def test_failed_pre_dispatch_write_prevents_tool_invocation(self):
        from unittest.mock import patch
        original=RunJournal.save
        def fail(journal,state,phase,**kw):
            if phase=='pending':raise OSError('disk full')
            return original(journal,state,phase,**kw)
        with patch.object(RunJournal,'save',fail),self.assertRaises(OSError):await self.run_loop()
        self.assertEqual(self.calls,{})
        result=await self.run_loop(resume=True)
        self.assertEqual(result['status'],'complete')

    async def test_fresh_run_cannot_overwrite_and_resume_requires_existing_id(self):
        with self.assertRaises(KeyError):await self.run_loop(resume=True)
        await self.run_loop()
        with self.assertRaises(RunConflict):await self.run_loop()
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    async def test_result_returned_but_not_committed_remains_uncertain(self):
        from unittest.mock import patch
        original=RunJournal.save
        for operation,next_node in [('model','execute'),('tool','decide')]:
            before=self.calls.copy();identifier='lost-result-'+operation
            def fail(journal,state,phase,**kw):
                if phase=='ready' and kw.get('next_node')==next_node and state['model_calls']:
                    raise OSError('commit failed after response')
                return original(journal,state,phase,**kw)
            with patch.object(RunJournal,'save',fail),self.assertRaises(OSError):await self.run_loop(identifier=identifier)
            result=await self.run_loop(identifier=identifier,resume=True)
            self.assertEqual(result['reason'],'uncertain_'+operation+'_dispatch')
            self.assertEqual(self.calls-before,{'model':1} if operation=='model' else {'model':1,'tool':1})

    async def test_cumulative_input_accounting_matches_uninterrupted_run(self):
        baseline=await self.run_loop(identifier='baseline-test')
        async def stop(snapshot):
            if snapshot['last_node']=='execute':raise Crash()
        with self.assertRaises(Crash):await self.run_loop(identifier='resumed-test',on_state=stop)
        resumed=await self.run_loop(identifier='resumed-test',resume=True)
        for key in ('model_calls','tool_calls','estimated_input_units','input_tokens','output_tokens','usage_reported_calls'):
            self.assertEqual(resumed[key],baseline[key],key)
