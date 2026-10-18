"""Evidence export preserves raw text and unresolved scope; never trusts client text."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_mineru_retrieval import build
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_report import export_report
from tcm.mineru_research import SnapshotChanged
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root / 'view')
        self.rebuild()

    def rebuild(self, change=None):
        import shutil
        if change:
            path = self.root / 'view/research_view.json'
            view = json.loads(path.read_text()); change(view)
            path.write_text(json.dumps(view, ensure_ascii=False))
            receipt = self.root / 'view/summary.json'
            meta = json.loads(receipt.read_text()); meta['research_view_sha256'] = digest(path)
            receipt.write_text(json.dumps(meta))
        shutil.rmtree(self.root / 'index', ignore_errors=True)
        build(self.root / 'view', self.root / 'index')
        self.service = Service(self.root / 'index')
        self.sha = self.service.receipt['index_sha256']

    def export(self, question='示例叶炭的功效'):
        return export_report(self.service, question, self.sha)

    def test_report_preserves_fields_and_physical_source_not_chapter_offset(self):
        before = digest(self.root / 'index/research.sqlite3')
        text = self.export()
        for value in ('全部未人工审核', '仅记录本轮查询字段', '测试用途', '物理页码（从 1 开始）：3',
                      '章节页码：1', 'OCR 原文', 'chunk_id', self.sha, 'a' * 64):
            self.assertIn(value, text)
        self.assertEqual(before, digest(self.root / 'index/research.sqlite3'))
        self.assertEqual(text, self.export())
        self.assertNotIn(str(self.root), text)

    def test_ai_revision_and_original_are_both_exported(self):
        def change(view):
            entry = view['entries'][1]
            field = entry['fields'][1]
            field['raw_text'] = '【功能与主治】原始错字。'
            field['overlay_ids'] = ['revision-1']; entry['applied_overlay_ids'] = ['revision-1']
        self.rebuild(change)
        text = self.export()
        self.assertIn('原始错字', text); self.assertIn('测试用途', text)
        self.assertIn('revision', text); self.assertIn('未人工批准', text)

    def test_reference_witness_kept_separate_without_copying_parent_fields(self):
        def change(view):
            fields = view['entries'][1]['fields']
            fields[0]['text'] = fields[0]['raw_text'] = '【用法与用量】'
            fields[1]['label'] = '贮藏'
            fields[1]['text'] = fields[1]['raw_text'] = '【贮藏】同示例叶。'
        self.rebuild(change)
        text = self.export('示例叶炭的用量')
        self.assertIn('reference', text); self.assertIn('相邻引用提示', text)
        self.assertIn('同示例叶', text); self.assertNotIn('另一炮制品用量', text)
        self.assertIn('不作为完整字段结果', text)

    def test_unknown_processed_scope_exports_only_parent_title(self):
        text = self.export('酒示例叶的用量')
        self.assertIn('指定炮制品范围未解析', text)
        self.assertIn('相关母条目', text)
        self.assertNotIn('测试用量', text); self.assertNotIn('另一炮制品用量', text)

    def test_absent_field_and_repeated_scope_never_become_complete_answers(self):
        missing = self.export('示例叶炭的注意')
        self.assertIn('未检出请求字段', missing); self.assertIn('不等于', missing)
        repeated = self.export('示例叶的用量')
        self.assertIn('范围存在歧义', repeated)
        self.assertIn('测试用量', repeated); self.assertIn('另一炮制品用量', repeated)
        mixed = self.export('示例叶炭的功效和注意')
        self.assertIn('本轮未返回以下请求字段：注意', mixed)

    def test_truncation_is_disclosed(self):
        def change(view):
            entry = view['entries'][1]
            entry['fields'] += [dict(entry['fields'][0]) for _ in range(5)]
        self.rebuild(change)
        self.assertIn('结果被截断：共 6 个字段，本文件只含前 5 个', self.export('示例叶炭的用量'))

    def test_stale_snapshot_and_changed_source_fail_closed(self):
        with self.assertRaises(SnapshotChanged):
            export_report(self.service, '示例叶炭的功效', '0' * 64)
        self.service.available = False
        with self.assertRaises(SnapshotChanged): self.export()

    def test_multiple_names_unsupported_queries_and_bad_parameters_are_rejected(self):
        for q in ('示例叶和示例叶炭的功效', '未知药的功效', '示例叶炭价格', '', '药' * 501):
            with self.subTest(q=q[:20]), self.assertRaises(ValueError): self.export(q)
        with self.assertRaises(ValueError): export_report(self.service, '示例叶炭的功效', '../secret')

    def test_source_markdown_fences_cannot_end_literal_block_or_inject_html(self):
        def change(view):
            field = view['entries'][1]['fields'][1]
            field['text'] = field['raw_text'] = '【功能与主治】\n```\n<img src=x onerror=alert(1)>\n```'
        self.rebuild(change)
        text = self.export('示例叶炭的功效 <img src=x onerror=alert(1)>')
        self.assertIn('````text\n【功能与主治】', text)
        self.assertIn('&lt;img', text)

    def test_main_api_download_stale_disable_and_invalid_parameters(self):
        from fastapi.testclient import TestClient
        import app
        with patch.object(app.research_runtime, 'get_service', return_value=self.service), TestClient(app.app) as client:
            params = {'q': '示例叶炭的功效', 'snapshot': self.sha}
            response = client.get('/api/research/report', params=params)
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/markdown', response.headers['content-type'])
            self.assertIn('attachment', response.headers['content-disposition'])
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertIn('测试用途', response.text)
            self.assertEqual(client.get('/api/research/report', params={**params, 'snapshot': '0'*64}).status_code, 409)
            self.assertEqual(client.get('/api/research/report', params={**params, 'path': 'secret'}).status_code, 400)
            self.assertEqual(client.get('/api/research/report', params=[*params.items(), ('q', 'other')]).status_code, 400)
            self.assertEqual(client.get('/api/research/report', params={**params, 'q': '未知药的功效'}).status_code, 400)
            self.assertEqual(client.get('/conversation').json()['messages'], [])  # Export never writes chat history.
            with patch.object(app.research_runtime, 'get_service', return_value=None):
                self.assertEqual(client.get('/api/research/report', params=params).status_code, 503)
            self.service.available = False
            self.assertEqual(client.get('/api/research/report', params=params).status_code, 409)
