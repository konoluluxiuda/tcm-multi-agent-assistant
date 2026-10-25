import asyncio
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from tcm.cross_source_report import fuse, run_report, text_search
from tcm.research_jobs import JobStore, JobConflict
from tests.test_mineru_retrieval import fixture
from scripts.build_mineru_retrieval import build


class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_branches_join_and_evidence(self):
        entered = set(); ready = asyncio.Event(); snapshots = []
        async def backend(name, herb, question):
            entered.add(name)
            if len(entered) == 2: ready.set()
            await asyncio.wait_for(ready.wait(), 1)
            return ({'status':'candidates','hits':[{'chunk_id':'x','text':'synthetic'}]} if name=='text'
                    else {'status':'name_mapping_candidates','rows':[{'value':'synthetic','source':{'json_pointer':'/x'}}]})
        async def save(state): snapshots.append(state)
        result = await run_report({'herb':'示例叶','question':'功效'},backend=backend,on_state=save)
        self.assertEqual(result['status'],'complete')
        self.assertFalse(result['identity_merged']);self.assertFalse(result['answer_generated'])
        self.assertEqual(snapshots[-1]['tool_calls'],2)
        self.assertEqual(result['structured']['rows'][0]['source']['json_pointer'],'/x')

    async def test_timeout_partial_and_no_evidence(self):
        async def backend(name,*args):
            if name=='text': raise asyncio.TimeoutError()
            return {'status':'ok','rows':[{'value':'kept'}]}
        result = await run_report({'herb':'x','question':'x'},backend=backend)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['text']['status'],'timeout')
        async def broken(*args): raise ValueError('private path')
        result = await run_report({'herb':'x','question':'x'},backend=broken)
        self.assertEqual(result['status'],'needs_input');self.assertNotIn('private path',str(result))

    async def test_cancellation_propagates(self):
        started=asyncio.Event();stopped=[]
        async def backend(name,*args):
            started.set()
            try: await asyncio.sleep(10)
            finally: stopped.append(name)
        task=asyncio.create_task(run_report({'herb':'x','question':'x'},backend=backend))
        await asyncio.wait_for(started.wait(), 2);await asyncio.sleep(.01);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(set(stopped),{'text','structured'})


class RetrievalTests(unittest.TestCase):
    def test_fusion_dedup_and_stable_ties(self):
        a={'chunk_id':'a'};b={'chunk_id':'b'}
        self.assertEqual([h['chunk_id'] for h in fuse([[a,b],[b,a]])],['a','b'])
        self.assertEqual(fuse([[a,a]])[0]['fusion_score'],1/61)

    def test_scoped_fields_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root/'source');build(root/'source',root/'index')
            with patch.dict('os.environ',{'PHARMACOPOEIA_INDEX':str(root/'index'),'LOCAL_DENSE_MODEL':'','LOCAL_DENSE_CACHE':''}):
                result=text_search('示例叶','功能与主治')
                self.assertEqual(result['status'],'candidates')
                self.assertTrue(all(h['name']=='示例叶' for h in result['hits']))
                self.assertEqual(text_search('示例叶','用量')['status'],'ambiguous_field')
                self.assertEqual(text_search('示例叶','示例叶炭功效')['status'],'multiple_entities')
                self.assertEqual(text_search('未知','功效')['hits'],[])
                self.assertEqual(text_search('示例叶','示例叶的功能与主治')['status'],'candidates')
                self.assertEqual(text_search('示例叶','市场价格')['hits'],[])

    def test_job_payload_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=JobStore(Path(tmp)/'jobs.db');p={'kind':'cross_source','herb':'x','question':'y'}
            job,created=store.enqueue('owner','x:y','key',payload=p)
            self.assertTrue(created);self.assertEqual(store.get('owner',job['id'])['payload'],p)
            self.assertFalse(store.enqueue('owner','x:y','key',payload=dict(reversed(list(p.items()))))[1])
            with self.assertRaises(JobConflict):store.enqueue('owner','x:y','key',payload={**p,'herb':'z'})
            with self.assertRaises(JobConflict):store.enqueue('owner','x:y','key')
