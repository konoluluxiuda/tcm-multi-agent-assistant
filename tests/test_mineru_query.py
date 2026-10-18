"""Bounded query language improvements must not invent names or relax source scope."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_mineru_retrieval import build
from tcm.mineru_chat import ResearchTool
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_report import export_report
from tcm.mineru_retrieval import ResearchRetriever
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root / 'view')
        self.path = self.root / 'view/research_view.json'
        view = json.loads(self.path.read_text())
        view['entries'][0].update(name_zh_candidate='党参', raw_name_zh_candidate='党参')
        herb = view['entries'][1]
        herb.update(name_zh_candidate='黄芪', raw_name_zh_candidate='黄芪')
        template = herb['fields'][0]
        for label in ('origin', '贮藏', '注意', '性味与归经'):
            herb['fields'].append(dict(deepcopy(template), label=label, text=f'【{label}】示例来源文字。',
                                       raw_text=f'【{label}】示例来源文字。'))
        self.view = view
        self.rebuild()

    def rebuild(self):
        import shutil
        self.path.write_text(json.dumps(self.view, ensure_ascii=False))
        summary = self.root / 'view/summary.json'
        receipt = json.loads(summary.read_text()); receipt['research_view_sha256'] = digest(self.path)
        summary.write_text(json.dumps(receipt))
        shutil.rmtree(self.root / 'index', ignore_errors=True)
        self.receipt = build(self.root / 'view', self.root / 'index')

    def query(self, question, **kwargs):
        r = ResearchRetriever(self.root / 'index')
        try: return r.query(question, **kwargs)
        finally: r.close()

    def test_field_paraphrases_route_to_source_labels(self):
        for suffix, label in [('每次多少克', '用法与用量'), ('服用剂量', '用法与用量'),
                              ('每次几克', '用法与用量'), ('有什么药效', '功能与主治'),
                              ('储藏条件', '贮藏'), ('使用警示', '注意'),
                              ('使用警告', '注意'), ('取自哪一部分', 'origin')]:
            with self.subTest(suffix=suffix):
                result = self.query('黄芪' + suffix)
                self.assertEqual(result['status'], 'candidates')
                self.assertEqual([h['label'] for h in result['hits']], [label])
                self.assertTrue(result['query_interpretation']['field_matches'])

    def test_script_pairs_return_indexed_names_and_original_evidence(self):
        canonical = self.query('黄芪的功效')
        for question in ('黃芪的功效', '黃 芪的功效'):
            result = self.query(question)
            self.assertEqual(result['question'], question)
            self.assertEqual(result['hits'], canonical['hits'])
            self.assertEqual(result['query_interpretation']['name_forms'][0]['rule'], 'script_variant')
        self.assertEqual(self.query('黨參的功效')['hits'][0]['name'], '党参')

    def test_nfkc_and_whitespace_still_work_without_mutating_index(self):
        before = digest(self.root / 'index/research.sqlite3')
        self.assertEqual(self.query(' 黄 芪 的 功效 ？')['status'], 'candidates')
        self.assertEqual(self.query('黄芪的功效')['status'], 'candidates')
        self.assertEqual(before, digest(self.root / 'index/research.sqlite3'))

    def test_unsupported_pinyin_and_ocr_typos_are_not_guessed(self):
        for query in ('Huangqi的功效', 'Dangshen的功效', 'Ｄａｎｇｓｈｅｎ的功效', '黄茋的功效'):
            result = self.query(query)
            self.assertEqual(result['status'], 'name_not_resolved', query)
            self.assertFalse(result['hits'])

    def test_script_normalization_does_not_bypass_processed_scope(self):
        for method in ('name_field', 'bm25'):
            result = self.query('酒黃芪的服用剂量', method=method)
            self.assertEqual(result['status'], 'variant_scope_unresolved')
            self.assertFalse(result['hits']); self.assertEqual(result['related_entries'][0]['name'], '黄芪')
        self.assertEqual(self.query('', name='酒黃芪', field='用法与用量')['status'], 'variant_scope_unresolved')

    def test_exact_preparation_keeps_longest_name_after_glyph_normalization(self):
        entry = deepcopy(self.view['entries'][1]); entry.update(id='prepared', name_zh_candidate='炙黄芪', raw_name_zh_candidate='炙黄芪')
        self.view['entries'].append(entry); self.rebuild()
        result = self.query('炙黃芪每次多少克')
        self.assertEqual(result['status'], 'candidates')
        self.assertEqual(result['hits'][0]['name'], '炙黄芪')

    def test_unknown_compound_and_multiple_names_are_preserved(self):
        for question in ('火星黃芪草的药效', '黃芪草的服用剂量'):
            for method in ('name_field', 'bm25'):
                result = self.query(question, method=method)
                self.assertEqual(result['status'], 'name_not_resolved'); self.assertFalse(result['hits'])
        result = self.query('黃芪和黨參的功效')
        self.assertEqual(result['status'], 'multiple_names'); self.assertFalse(result['hits'])

    def test_glyph_collision_between_source_names_requires_clarification(self):
        entry = deepcopy(self.view['entries'][1]); entry.update(id='other', name_zh_candidate='黃芪', raw_name_zh_candidate='黃芪')
        self.view['entries'].append(entry); self.rebuild()
        for result in (self.query('黃芪的药效'), self.query('', name='黃芪', field='功能与主治')):
            self.assertEqual(result['status'], 'ambiguous_name'); self.assertFalse(result['hits'])

    def test_explicit_filter_accepts_only_supported_glyph_equivalence(self):
        self.assertEqual(self.query('', name='黃芪', field='功能与主治')['hits'][0]['name'], '黄芪')
        self.assertEqual(self.query('', name='Huangqi', field='功能与主治')['status'], 'name_not_resolved')

    def test_out_of_scope_queries_still_fail_and_multiple_fields_stay_separate(self):
        self.assertEqual(self.query('黃芪今天的价格')['status'], 'field_not_resolved')
        result = self.query('黃芪的药效与使用警示')
        self.assertEqual({h['label'] for h in result['hits']}, {'功能与主治', '注意'})
        self.assertFalse(result['answer_generated'])

    def test_missing_and_reference_fields_remain_incomplete_with_new_wording(self):
        self.assertEqual(self.query('黨參的使用警示')['status'], 'field_not_observed')
        field = self.view['entries'][1]['fields'][0]
        field['text'] = field['raw_text'] = '【用法与用量】同党参。'; self.rebuild()
        result = self.query('黃芪每次多少克')
        self.assertEqual(result['status'], 'reference_unresolved'); self.assertFalse(result['hits'])

    def test_name_only_context_and_report_keep_canonical_focus_and_query_trace(self):
        service = Service(self.root / 'index'); tool = ResearchTool(service)
        result, context = tool.execute('黃芪', None)
        self.assertEqual(result['focus_name'], '黄芪')
        result, _ = tool.execute('它的服用剂量', context)
        self.assertEqual(result['resolved_question'], '黄芪的服用剂量')
        text = export_report(service, '黃芪的服用剂量', service.receipt['index_sha256'])
        self.assertIn('黃芪 → 黄芪', text); self.assertIn('剂量 → 用法与用量', text)
        self.assertIn('OCR 原文', text)
