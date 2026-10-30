import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock
from experiments.architecture_candidates.offload_loop import execute
from experiments.architecture_candidates.offload_driver import Driver
from experiments.architecture_candidates.evidence_store import Scope
from tests.large_observation_fixture import fixture,MARKER
from tcm.research_run import Limits
from tcm.research_run_durable import RunConflict

class Paused(Exception):pass
class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);self.path=root/'run.sqlite3';self.ep=root/'evidence.sqlite3'
        self.scope=Scope('owner','loop-run-001','context-synthetic-v1','synthetic')
        self.backend=AsyncMock(return_value=fixture(14000))
    async def go(self,**kw):
        return await execute(kw.pop('sdk',Driver()),self.backend,path=self.path,evidence_path=self.ep,
            scope=kw.pop('scope',self.scope),model_contract='synthetic-v1',source_id='etcm-disease:'+'a'*64,**kw)
    async def pause(self,boundary):
        async def hook(name):
            if name==boundary:raise Paused()
        with self.assertRaises(Paused):await self.go(hook=hook)
    async def test_large_end_to_end(self):
        r=await self.go();self.assertEqual(r['status'],'complete');self.assertIsNone(r['inline'])
        self.assertEqual(r['read_result']['data']['items'],[MARKER])
        self.assertEqual((r['model_calls'],r['tool_calls']),(3,2))
        self.assertLess(max(r['model_input_bytes']),10000)
        self.assertEqual(self.backend.await_count,1)
    async def test_small_inline(self):
        self.backend.return_value=fixture(100)
        r=await self.go(sdk=Driver(50),offset=50)
        self.assertEqual(r['status'],'complete');self.assertIsNone(r['reference'])
        self.assertEqual(r['read_result']['data']['items'],[MARKER])
        with sqlite3.connect(self.ep) as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],0)
    async def test_forged_handle(self):
        r=await self.go(sdk=Driver(attack='handle'))
        self.assertEqual(r['reason'],'unknown_evidence_handle');self.assertEqual(r['tool_calls'],1)
    async def test_wrong_slice(self):
        r=await self.go(sdk=Driver(attack='offset'));self.assertEqual(r['reason'],'read_out_of_scope')
    async def test_forged_citation(self):
        r=await self.go(sdk=Driver(attack='citation'));self.assertEqual(r['reason'],'unverified_finish')
    async def test_early_finish(self):
        r=await self.go(sdk=Driver(attack='early_finish'));self.assertEqual(r['reason'],'unverified_finish')
    async def test_model_budget(self):
        r=await self.go(limits=Limits(model_calls=2));self.assertEqual(r['reason'],'model_budget')
    async def test_tool_budget(self):
        r=await self.go(limits=Limits(tool_calls=1));self.assertEqual(r['reason'],'tool_budget')
    async def test_reference_resume_and_terminal_replay(self):
        await self.pause('reference_saved');r=await self.go(resume=True)
        self.assertEqual(r['status'],'complete');self.assertEqual(self.backend.await_count,1)
        r=await self.go(resume=True);self.assertEqual((r['model_calls'],r['tool_calls']),(3,2))
    async def test_unpublished_result_stops(self):
        await self.pause('evidence_saved');r=await self.go(resume=True)
        self.assertEqual(r['reason'],'uncertain_tool_dispatch');self.assertEqual(self.backend.await_count,1)
    async def test_saved_action_resumes_without_extra_model(self):
        await self.pause('action_saved');r=await self.go(resume=True)
        self.assertEqual(r['status'],'complete');self.assertEqual(r['model_calls'],3)
    async def test_missing_evidence_prevents_resume(self):
        await self.pause('reference_saved')
        with sqlite3.connect(self.ep) as db:db.execute('DELETE FROM evidence')
        r=await self.go(resume=True);self.assertEqual(r['reason'],'published_evidence_unavailable')
        self.assertEqual(r['model_calls'],1)
    async def test_owner_and_version_mismatch(self):
        await self.pause('reference_saved')
        with self.assertRaises(KeyError):await self.go(resume=True,scope=replace(self.scope,owner='other'))
        with self.assertRaises(RunConflict):await self.go(resume=True,scope=replace(self.scope,catalog_revision='changed'))
    async def test_wrong_source(self):
        data=self.backend.return_value['data'];data['record_id']='etcm-disease:'+'b'*64
        data['profile']['source']['file_sha256']='b'*64
        r=await self.go();self.assertEqual(r['reason'],'evidence_storage_or_validation_failed')
