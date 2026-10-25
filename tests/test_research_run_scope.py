import asyncio
import copy
import unittest
from unittest.mock import patch,AsyncMock
from langchain_core.messages import AIMessage
from tcm.research_run import run
from tcm.research_run_demo import synthetic_tool,SID
from tcm.research_run_scope import RunScope
from tcm.research_run_local import DeclaredActionsDriver,LocalDiseaseTools,run_local_profile
from tests import test_disease_catalog as fixtures
from tcm.target_similarity import SnapshotChanged


def scope(extra=None):
    return RunScope.model_validate({'requirements':[{'id':'selected-source','call':{'tool':'disease_gene_profile','arguments':{'name':SID}}}],
                                    'unhandled_requirements':extra or []})


class ScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_scope_and_unhandled_requirement(self):
        for extra,status in (([],'complete'),(['compare another herb'],'partial')):
            s=scope(extra);r=await run('q',DeclaredActionsDriver(s),synthetic_tool,scope=s)
            self.assertEqual(r['status'],status)
            self.assertTrue(r['coverage']['requirements'][0]['fulfilled'])
            self.assertEqual(r['coverage']['unhandled_requirements'],extra)

    async def test_wrong_source_is_blocked_before_tool_execution(self):
        s=scope();other=copy.deepcopy(s);other.requirements[0].call['arguments']['name']='etcm-disease:'+'b'*64
        backend=AsyncMock()
        r=await run('q',DeclaredActionsDriver(other),backend,scope=s)
        self.assertEqual(r['reason'],'out_of_scope');backend.assert_not_awaited()
        self.assertFalse(r['coverage']['complete'])

    async def test_missing_cited_evidence_cannot_complete(self):
        s=scope()
        class Driver(DeclaredActionsDriver):
            async def ainvoke(self,messages):
                r=await super().ainvoke(messages)
                if r.tool_calls[0]['name']=='finish_research':r.tool_calls[0]['args']['evidence_ids']=[]
                return r
        r=await run('q',Driver(s),synthetic_tool,scope=s)
        self.assertEqual(r['status'],'partial');self.assertFalse(r['coverage']['complete'])

    async def test_wrong_result_identity_is_not_evidence(self):
        s=scope()
        async def wrong(n,a):
            r=await synthetic_tool(n,a)
            r['data']['record_id']='etcm-disease:'+'b'*64;r['data']['profile']['source']['file_sha256']='b'*64
            return r
        r=await run('q',DeclaredActionsDriver(s),wrong,scope=s)
        self.assertEqual(r['status'],'partial');self.assertEqual(r['evidence']['obs-1']['status'],'error')

    async def test_empty_is_completed_query_not_missing_query(self):
        s=scope()
        async def empty(n,a):
            r=await synthetic_tool(n,a);r['status']='empty';r['data']['target_ids']=[];r['data']['profile']['target_count']=0
            return r
        r=await run('q',DeclaredActionsDriver(s),empty,scope=s)
        self.assertEqual(r['status'],'complete');self.assertEqual(r['evidence']['obs-1']['status'],'empty')

    def test_scope_requires_resolved_identity_and_unique_requirement_ids(self):
        with self.assertRaises(ValueError):RunScope.model_validate({'requirements':[{'id':'a','call':{'tool':'disease_gene_profile','arguments':{'name':'free name'}}}]})
        s=scope().model_dump();s['requirements']*=2
        with self.assertRaises(ValueError):RunScope.model_validate(s)

    def make_db(self):
        f=fixtures.CatalogTests();f.setUp();self.addCleanup(f.doCleanups)
        return f.build()[0]

    async def test_real_worker_fixture_and_unchanged_database(self):
        db=self.make_db();before=db.read_bytes()
        result=await run_local_profile(db,'Synthetic disease')
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['run']['evidence']['obs-1']['data']['target_ids'],['P12345','Q12345'])
        self.assertEqual(before,db.read_bytes())
        self.assertEqual((await run_local_profile(db,'unknown'))['status'],'needs_input')

    async def test_snapshot_change_is_refused(self):
        import sqlite3
        db=self.make_db();backend=LocalDiseaseTools(db)
        with sqlite3.connect(db) as con:con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='source_fingerprint'")
        with self.assertRaises(SnapshotChanged):await backend('search_disease_sources',{'name':'Synthetic'})

    async def test_worker_cancellation_reaps_child(self):
        db=self.make_db();backend=LocalDiseaseTools(db);started=asyncio.Event()
        class Child:
            returncode=None
            killed=False
            async def communicate(self,data):started.set();await asyncio.Future()
            def kill(self):self.killed=True
            async def wait(self):self.returncode=-9
        child=Child()
        with patch('asyncio.create_subprocess_exec',new=AsyncMock(return_value=child)) as spawn:
            task=asyncio.create_task(backend('search_disease_sources',{'name':'Synthetic'}))
            await started.wait();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(child.killed);self.assertEqual(child.returncode,-9)
            self.assertNotIn('CHAT_API_KEY',spawn.call_args.kwargs['env'])
