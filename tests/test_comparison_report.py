"""Comparison exports re-query frozen sources without accepting browser evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_mineru_retrieval import build
from tcm.comparison_report import export_comparison
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_research import SnapshotChanged
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture


class ComparisonReportTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        fixture(self.root/'view');build(self.root/'view',self.root/'index')
        self.service=Service(self.root/'index');self.sha=self.service.receipt['index_sha256']

    def export(self,question='比较示例叶和示例叶炭的功效'):
        return export_comparison(self.service,question,self.sha)

    def test_originals_and_each_herb_provenance_are_separate_and_readonly(self):
        before=digest(self.root/'index/research.sqlite3');text=self.export()
        self.assertIn('## 药材：示例叶\n',text);self.assertIn('## 药材：示例叶炭\n',text)
        for value in ('测试用途','全部未人工审核','OCR 原文','研究视图','物理页码（从 1 开始）','chunk_id',self.sha,'a'*64,'模型调用：0'):
            self.assertIn(value,text)
        self.assertEqual(len(self.service.queries),3)
        self.assertEqual(text,self.export());self.assertEqual(before,digest(self.root/'index/research.sqlite3'))
        self.assertNotIn(str(self.root),text)

    def test_missing_and_repeated_fields_keep_partial_status_without_filling(self):
        missing=self.export('比较示例叶和示例叶炭的功效与注意')
        self.assertIn('partial',missing);self.assertEqual(missing.count('field\\_not\\_observed'),2)
        self.assertEqual(missing.count('本单元没有可用候选字段'),2)
        repeated=self.export('比较示例叶和示例叶炭的功效与用量')
        self.assertIn('ambiguous\\_field',repeated);self.assertIn('partial\\_evidence',repeated)
        self.assertEqual(len(self.service.queries),10)  # two bounded 1+4 query comparisons

    def test_invalid_scope_and_stale_index_are_rejected_before_any_export(self):
        for question in ('示例叶的功效','比较示例叶和示例叶炭哪个更适合我','比较示例叶和示例叶炭的功效与用量与注意与归经与贮藏'):
            with self.assertRaises(ValueError):self.export(question)
        self.assertEqual(self.service.queries,[])
        with self.assertRaises(SnapshotChanged):export_comparison(self.service,'比较示例叶和示例叶炭的功效','f'*64)
        self.assertEqual(self.service.queries,[])
        for question in ('比较酒示例叶和示例叶炭的功效','比较示例叶和外星测试草的功效'):
            with self.assertRaises(ValueError):self.export(question)

    def test_source_change_during_render_discards_entire_document(self):
        from tcm.mineru_report import field_section
        def change(*args):
            result=field_section(*args);self.service.available=False;return result
        with patch('tcm.comparison_report.field_section',side_effect=change):
            with self.assertRaises(SnapshotChanged):self.export()

    def test_oversized_export_is_rejected_without_silent_truncation(self):
        with patch('tcm.comparison_report.field_section',return_value=['x'*(3*1024*1024)]):
            with self.assertRaises(ValueError):self.export()

    def test_api_download_is_stateless_rejects_injected_parameters_and_fails_closed(self):
        import app
        from fastapi.testclient import TestClient
        from agents.conversation_store import ConversationStore
        def forbidden(*a,**kw):raise AssertionError('Export invoked chat/model')
        memory=ConversationStore(self.root/'memory.sqlite3')
        with patch.object(app.agent_runtime,'memory',memory),patch.object(app.research_runtime,'get_service',return_value=self.service), \
             patch.object(app,'process_query',side_effect=forbidden),TestClient(app.app) as client:
            params={'q':'比较示例叶和示例叶炭的功效与注意','snapshot':self.sha}
            response=client.get('/api/research/comparison-report',params=params)
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('attachment',response.headers['Content-Disposition']);self.assertEqual(response.headers['Cache-Control'],'no-store')
            self.assertNotIn('set-cookie',response.headers)
            self.assertIn('partial',response.text)
            self.assertEqual(client.get('/conversation').json()['messages'],[])
            for bad in ({**params,'hits':'fabricated'},list(params.items())+[('q','伪造')]):
                self.assertEqual(client.get('/api/research/comparison-report',params=bad).status_code,400)
            self.assertEqual(client.get('/api/research/comparison-report',params={**params,'snapshot':'f'*64}).status_code,409)
            with patch.object(app.research_runtime,'get_service',return_value=None):
                self.assertEqual(client.get('/api/research/comparison-report',params=params).status_code,503)


if __name__=='__main__':unittest.main()
