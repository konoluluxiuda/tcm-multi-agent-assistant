import tempfile
from pathlib import Path
import sqlite3
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock
from tests.large_observation_fixture import fixture,MARKER
from experiments.architecture_candidates.evidence_store import Scope
from experiments.architecture_candidates.offload_journal import execute
from tcm.research_run_durable import RunConflict

class Paused(Exception):pass

class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);self.path=root/'run.sqlite3';self.ep=root/'evidence.sqlite3'
        self.scope=Scope('owner','run-test-001','context-synthetic-v1','synthetic')
        self.backend=AsyncMock(return_value=fixture(14000))
    async def go(self,**kw):
        return await execute(path=self.path,evidence_path=self.ep,scope=kw.pop('scope',self.scope),backend=self.backend,**kw)
    async def pause(self,boundary):
        async def hook(name):
            if name==boundary:raise Paused()
        with self.assertRaises(Paused):await self.go(hook=hook)
    async def test_published_resume_reads_without_fetch(self):
        await self.pause('reference_saved');r=await self.go(resume=True)
        self.assertEqual(r['status'],'complete');self.assertEqual(r['result']['data']['items'],[MARKER])
        self.assertEqual(self.backend.await_count,1)
    async def test_unpublished_record_stops_without_refetch(self):
        await self.pause('evidence_saved');r=await self.go(resume=True)
        self.assertEqual(r['reason'],'uncertain_tool_dispatch');self.assertIsNone(r['reference'])
        self.assertEqual(self.backend.await_count,1)
        with sqlite3.connect(self.ep) as db:self.assertEqual(db.execute('SELECT count(*) FROM evidence').fetchone()[0],1)
    async def test_missing_published_record_is_partial(self):
        await self.pause('reference_saved')
        with sqlite3.connect(self.ep) as db:db.execute('DELETE FROM evidence')
        r=await self.go(resume=True)
        self.assertEqual(r['reason'],'published_evidence_unavailable');self.assertIsNone(r['result'])
        self.assertEqual(self.backend.await_count,1)
    async def test_corrupt_record_is_partial(self):
        await self.go()
        with sqlite3.connect(self.ep) as db:db.execute("UPDATE evidence SET payload='{}'")
        r=await self.go(resume=True)
        self.assertEqual(r['status'],'partial');self.assertIsNone(r['result'])
    async def test_owner_and_version_binding(self):
        await self.pause('reference_saved')
        with self.assertRaises(KeyError):await self.go(resume=True,scope=replace(self.scope,owner='other'))
        with self.assertRaises(RunConflict):await self.go(resume=True,scope=replace(self.scope,catalog_revision='new'))
    async def test_terminal_resume_does_not_fetch(self):
        await self.go();r=await self.go(resume=True)
        self.assertEqual(r['status'],'complete');self.assertEqual(self.backend.await_count,1)
