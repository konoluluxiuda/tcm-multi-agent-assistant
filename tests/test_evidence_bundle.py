"""Combined exports must be scoped, literal and bound to the current session."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from tcm.agent_pubmed import _hash
from tcm.evidence_bundle import export_bundle
from tcm.mineru_research import SnapshotChanged
from scripts.build_mineru_retrieval import build
from tests import test_agent_pubmed as pubmed_tests
from tests.test_literature_evidence import parsed, relation
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture
from tests.test_pubmed import client as fixture_client


class EvidenceBundleTests(unittest.TestCase):
    def setUp(self):
        pubmed_tests.PubMedGraphTests.setUp(self)
        from fastapi.testclient import TestClient
        import app
        self.app=app
        fixture(self.root/'view'); build(self.root/'view',self.root/'index')
        self.service=Service(self.root/'index')
        self.sha=self.service.receipt['index_sha256']
        self.env=patch.dict('os.environ',{'ENABLE_PHARMACOPOEIA_RESEARCH':'1','RESEARCH_TOOLS_ONLY':'1'})
        self.env.start();self.addCleanup(self.env.stop)
        p=patch.object(app.research_runtime,'get_service',return_value=self.service)
        p.start();self.addCleanup(p.stop)
        self.api=TestClient(app.app); self.api.__enter__()
        self.addCleanup(lambda:self.api.__exit__(None,None,None))
        response=self.api.post('/chat',json={'query':'/pubmed fixture-herb'})
        self.assertEqual(response.status_code,200,response.text)
        self.batch=response.json()['pubmed']['tool_result']
        self.body={'question':'示例叶炭的功效','index_sha256':self.sha,'run_id':self.batch['run_id']}

    def post(self,body=None):
        return self.api.post('/api/evidence/report',json=self.body if body is None else body)

    def test_download_separates_sources_and_never_changes_history(self):
        before=self.api.get('/conversation').json()
        with patch('tcm.agent_pubmed.PubMedClient',side_effect=AssertionError('Export must not query PubMed')):
            response=self.post()
        self.assertEqual(response.status_code,200,response.text)
        for value in ('研究对象对应关系尚未核验','现代文献','药典单药证据清单','测试用途',
                      '物理页码（从 1 开始）：3','https://pubmed.ncbi.nlm.nih.gov/123/',self.sha,
                      '未读取全文','不将不同PMID','原始 PDF'):
            self.assertIn(value,response.text)
        self.assertNotIn('Fixture result, not clinical advice.',response.text)
        self.assertNotIn(str(self.root),response.text)
        self.assertIn('attachment',response.headers['content-disposition'])
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')
        self.assertEqual(self.api.get('/conversation').json(),before)

    def test_another_cookie_or_guessed_run_id_cannot_read_batch(self):
        from fastapi.testclient import TestClient
        with TestClient(self.app.app) as other:
            self.assertEqual(other.post('/api/evidence/report',json=self.body).status_code,401)
            other.get('/conversation')
            self.assertEqual(other.post('/api/evidence/report',json=self.body).status_code,409)
        self.assertEqual(self.post(dict(self.body,run_id='0'*32)).status_code,409)

    def test_old_card_cannot_export_after_new_batch_task_switch_or_delete(self):
        self.api.post('/chat',json={'query':'/pubmed another-query'})
        self.assertEqual(self.post().status_code,409)
        self.api.post('/chat',json={'query':'/research-clear'})
        self.assertEqual(self.post().status_code,409)
        self.api.delete('/conversation')
        self.assertEqual(self.post().status_code,409)

    def test_stale_sources_disabled_tools_and_tampered_batch_fail(self):
        self.assertEqual(self.post(dict(self.body,index_sha256='0'*64)).status_code,409)
        self.service.available=False
        self.assertEqual(self.post().status_code,409)
        self.service.available=True
        with patch.dict('os.environ',{'ENABLE_PUBMED':'0'}):
            self.assertEqual(self.post().status_code,503)
        with patch.object(self.app.research_runtime,'get_service',return_value=None):
            self.assertEqual(self.post().status_code,503)
        with sqlite3.connect(self.root/'literature.sqlite3') as db:
            db.execute("UPDATE search_runs SET payload='{}'")
        self.assertEqual(self.post().status_code,409)

    def test_arbitrary_text_paths_and_ambiguous_questions_are_rejected(self):
        for field in ('path','articles','messages','source','snapshot_sha256'):
            self.assertEqual(self.post(dict(self.body,**{field:'private'})).status_code,422)
        self.assertEqual(self.api.post('/api/evidence/report?path=private',json=self.body).status_code,400)
        for question in ('未知药的功效','示例叶和示例叶炭的功效','', '药'*501):
            self.assertIn(self.post(dict(self.body,question=question)).status_code,(400,422))

    def test_revision_change_during_export_is_not_returned_as_current(self):
        from tcm import evidence_bundle
        original=evidence_bundle.export_bundle
        def race(*args):
            text=original(*args)
            cookie=self.api.cookies.get(self.app.SESSION_COOKIE_NAME)
            current=self.memory.load(cookie)
            self.memory.save(cookie,current.messages,current.tcm_context,revision=current.revision)
            return text
        with patch.object(evidence_bundle,'export_bundle',side_effect=race):
            self.assertEqual(self.post().status_code,409)

    def render(self,result):
        result=dict(result,run_id='a'*32,replayed=True)
        stored={k:v for k,v in result.items() if k not in ('run_id','replayed')}
        return export_bundle(self.service,self.body['question'],self.sha,result,_hash(stored))

    def test_retraction_identity_and_legacy_scope_are_preserved(self):
        result=parsed(relation('RetractionIn'))
        result['articles'][0]['identity']['same_doi_other_pmids']=['456']
        text=self.render(result)
        for value in ('已撤稿','RetractionIn','456','未质量评议','未合并'):
            self.assertIn(value,text)
        result['articles'][0].pop('assessment')
        self.assertIn('旧批次缺少本版关系核查记录',self.render(result))

    def test_zero_error_partial_and_truncation_remain_explicit(self):
        self.assertIn('零命中',self.render(fixture_client(ids=[]).search('zero')))
        self.assertIn('检索失败不能据此判断',self.render(fixture_client(xml='<bad').search('failed')))
        partial=fixture_client(ids=['123','456']).search('partial')
        self.assertIn('未能获取的PMID',self.render(partial))
        partial['articles']=[deepcopy(partial['articles'][0]) for _ in range(6)]
        self.assertIn('本清单仅列前5条',self.render(partial))

    def test_source_markdown_and_html_remain_literal(self):
        result=parsed(relation('ErratumIn'))
        result['query']='```\n<script>alert(1)</script>'
        item=result['articles'][0]
        item['title']='[fake](javascript:alert(1)) <script>sourceExecuted=true</script>'
        item['relations'][0]['note']='![x](https://external.test/image)'
        text=self.render(result)
        self.assertNotIn('<script>',text)
        self.assertNotIn('[fake](javascript:',text)
        self.assertNotIn('![x](https:',text)
        self.assertIn('&lt;script',text)

    def test_builder_rejects_modified_batch_hash(self):
        data=dict(fixture_client().search('fixture'),run_id='a'*32,replayed=True)
        with self.assertRaises(SnapshotChanged):
            export_bundle(self.service,self.body['question'],self.sha,data,'0'*64)


if __name__ == '__main__': unittest.main()
