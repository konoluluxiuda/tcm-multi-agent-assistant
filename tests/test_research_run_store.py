import asyncio
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from tcm.research_run_store import RunStore,RunConflict
from tcm.research_run import run
from tcm.research_run_demo import OfflineModel,synthetic_tool
from tcm.research_run_durable import local_profile


class JournalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'runs.sqlite3';self.now=1000
        self.store=RunStore(self.path,clock=lambda:self.now)

    async def test_steps_and_final_citations_survive_reopen(self):
        doc=self.store.create('alice','Synthetic task')
        async def persist(snapshot):
            nonlocal doc
            doc=self.store.save('alice',doc,snapshot=snapshot)
        result=await run('Synthetic task',OfflineModel(),synthetic_tool,on_state=persist)
        doc=self.store.save('alice',doc,result=result,status=result['status'])
        saved=RunStore(self.path).get('alice',doc['id'])
        self.assertEqual(saved['status'],'complete')
        self.assertEqual(saved['result']['events'][-1]['evidence_ids'],['obs-2','obs-3'])
        self.assertNotIn('messages',saved['snapshot']);self.assertNotIn('calls',saved['snapshot'])
        with self.assertRaises(KeyError):self.store.get('bob',doc['id'])
        self.assertEqual(self.store.list('bob'),[])

    async def test_conflicting_writes_and_stale_recovery(self):
        old=self.store.create('alice','q')
        with self.assertRaises(RunConflict):self.store.recover('alice',old['id'])
        new=self.store.save('alice',old,snapshot={'tool_calls':1})
        with self.assertRaises(RunConflict):self.store.save('alice',old,status='complete')
        self.now+=181
        recovered=RunStore(self.path,clock=lambda:self.now).recover('alice',old['id'])
        self.assertEqual(recovered['status'],'interrupted')
        self.assertEqual(recovered['snapshot']['tool_calls'],1)
        with self.assertRaises(RunConflict):self.store.save('alice',new,status='complete')

    async def test_storage_failure_stops_before_tool_execution(self):
        calls=[]
        async def backend(n,a):calls.append(n);return await synthetic_tool(n,a)
        async def broken(s):raise OSError('disk full')
        with self.assertRaises(OSError):await run('q',OfflineModel(),backend,on_state=broken)
        self.assertEqual(calls,[])

    async def test_wrapper_records_cancel_and_failure(self):
        async def cancelled(*a,**kw):raise asyncio.CancelledError()
        with patch('tcm.research_run_durable.run_local_profile',new=cancelled):
            with self.assertRaises(asyncio.CancelledError):await local_profile(self.store,'alice','unused','cancel')
        self.assertEqual(self.store.list('alice')[0]['status'],'cancelled')
        async def failed(*a,**kw):raise ValueError('private-path')
        with patch('tcm.research_run_durable.run_local_profile',new=failed):
            with self.assertRaises(ValueError):await local_profile(self.store,'bob','unused','failure')
        entry=self.store.list('bob')[0]
        self.assertEqual(entry['status'],'failed');self.assertNotIn('private-path',json.dumps(entry))

    def test_unrelated_database_refused_without_mutation(self):
        other=self.path.with_name('other.sqlite3')
        with sqlite3.connect(other) as db:db.execute('CREATE TABLE important(value TEXT)')
        before=other.read_bytes()
        with self.assertRaises(ValueError):RunStore(other)
        self.assertEqual(other.read_bytes(),before)
