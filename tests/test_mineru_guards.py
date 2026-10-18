"""Scope guards must preserve exact preparations, evidence and normal query behavior."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_mineru_retrieval import build
from tcm.mineru_guards import reference_target
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_retrieval import ResearchRetriever
from tests.test_mineru_retrieval import fixture


class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); cls.root = Path(cls.temp.name)
        fixture(cls.root / 'view')
        view_path = cls.root / 'view/research_view.json'
        view = json.loads(view_path.read_text())
        entry = deepcopy(view['entries'][1]); entry.update(id='reference', name_zh_candidate='炙示例叶', raw_name_zh_candidate='炙示例叶')
        template = deepcopy(entry['fields'][0])
        entry['fields'] = []
        for label, text in [('功能与主治', '【功能与主治】测试用途。'), ('用法与用量', '【用法与用量】'),
                            ('注意', '【注意】'), ('贮藏', '【贮藏】同示例叶。')]:
            entry['fields'].append(dict(deepcopy(template), label=label, text=text, raw_text=text))
        view['entries'].append(entry)
        view_path.write_text(json.dumps(view))
        summary = cls.root / 'view/summary.json'; data = json.loads(summary.read_text())
        data['research_view_sha256'] = digest(view_path); summary.write_text(json.dumps(data))
        build(cls.root / 'view', cls.root / 'index')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.r = ResearchRetriever(self.root / 'index')

    def tearDown(self):
        self.r.close()

    def test_unknown_preparation_is_not_replaced_in_either_method(self):
        for prefix in ('酒', '蜜', '盐', '醋', '酒炙'):
            for method in ('name_field', 'bm25'):
                with self.subTest(prefix=prefix, method=method):
                    result = self.r.query(prefix + '示例叶的用量', method=method)
                    self.assertEqual(result['status'], 'variant_scope_unresolved')
                    self.assertEqual(result['hits'], [])
                    self.assertEqual(result['related_entries'][0]['name'], '炙示例叶' if prefix == '酒炙' else '示例叶')
                    self.assertTrue(result['related_entries'][0]['title_evidence'])
                    self.assertFalse(result['lookup_complete'])

    def test_exact_preparation_remains_searchable(self):
        result = self.r.query('炙示例叶的功能与主治')
        self.assertEqual(result['status'], 'candidates')
        self.assertEqual(result['hits'][0]['name'], '炙示例叶')

    def test_explicit_name_cannot_bypass_variant_guard(self):
        result = self.r.query('', name='酒示例叶', field='用法与用量')
        self.assertEqual(result['status'], 'variant_scope_unresolved')
        self.assertFalse(result['hits'])

    def test_unknown_compound_name_is_not_a_known_substring(self):
        for q in ('火星示例叶草的功效', '火星示例叶的功效', '示例叶草的功效', '火星示例叶草和示例叶炭的功效'):
            for method in ('name_field', 'bm25'):
                with self.subTest(q=q, method=method):
                    result = self.r.query(q, method=method)
                    self.assertEqual(result['status'], 'name_not_resolved')
                    self.assertFalse(result['hits'])

    def test_supported_question_contexts_and_multiple_names_survive(self):
        for q in ('药典中示例叶炭的功效', '请问示例叶炭有什么作用？', '查询示例叶炭的功能', '示 例 叶 炭的主治'):
            self.assertEqual(self.r.query(q)['status'], 'candidates', q)
        self.assertEqual(self.r.query('比较示例叶与示例叶炭的用量')['status'], 'multiple_names')
        self.assertEqual(self.r.query('我该用示例叶还是示例叶炭？')['status'], 'multiple_names')
        self.assertEqual(self.r.query('我该用示例叶还是示例叶炭治疗？')['status'], 'multiple_names')
        self.assertEqual(self.r.query('示例叶炭该怎样保存？')['status'], 'field_not_observed')
        self.assertEqual(self.r.query('示例叶炭配伍禁忌')['status'], 'field_not_observed')
        self.assertEqual(self.r.query('示例叶炭含量测定')['status'], 'field_not_observed')

    def test_shared_reference_retains_original_header_and_separate_witness(self):
        result = self.r.query('炙示例叶的用量')
        self.assertEqual(result['status'], 'reference_unresolved'); self.assertFalse(result['hits'])
        blocked = result['unresolved_evidence'][0]
        self.assertEqual(blocked['raw_text'], '【用法与用量】')
        self.assertEqual(blocked['reference_target_candidate'], '示例叶')
        self.assertEqual(blocked['reference_witness']['raw_text'], '【贮藏】同示例叶。')
        self.assertTrue(blocked['reference_witness']['evidence'])
        self.assertFalse(result['answer_generated'])

    def test_direct_reference_is_not_automatically_expanded(self):
        result = self.r.query('炙示例叶怎么贮藏')
        self.assertEqual(result['status'], 'reference_unresolved')
        self.assertEqual(result['unresolved_evidence'][0]['text'], '【贮藏】同示例叶。')
        self.assertNotIn('reference_witness', result['unresolved_evidence'][0])

    def test_partial_multi_field_query_separates_incomplete_fields(self):
        result = self.r.query('炙示例叶的功效和用量')
        self.assertEqual(result['status'], 'partial_evidence')
        self.assertEqual([h['label'] for h in result['hits']], ['功能与主治'])
        self.assertEqual(len(result['unresolved_evidence']), 1)
        self.assertFalse(result['lookup_complete'])

    def test_bm25_never_returns_empty_headers_as_ordinary_hits(self):
        result = self.r.query('炙示例叶用法用量', method='bm25', k=20)
        self.assertTrue(result['unresolved_evidence'])
        self.assertFalse(any(h['name'] == '炙示例叶' and h['label'] == '用法与用量' for h in result['hits']))

    def test_reference_pattern_does_not_treat_cautions_as_cross_reference(self):
        self.assertIsNone(reference_target('【注意】不宜与其他药同用。'))
        self.assertEqual(reference_target('【贮藏】同药材。'), '药材')
        self.assertEqual(reference_target('【用法与用量】【注意】【贮藏】同示例叶。'), '示例叶')
