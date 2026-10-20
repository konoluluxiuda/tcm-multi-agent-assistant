"""Natural use questions must preserve exact entity and source scope guards."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_mineru_retrieval import build
from tcm.assistant_router import resolve
from tcm.mineru_retrieval import ResearchRetriever, usage_paraphrase
from tests.test_mineru_retrieval import fixture


class UsageParaphraseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        fixture(root / 'source')
        build(root / 'source', root / 'index')
        self.r = ResearchRetriever(root / 'index')
        self.addCleanup(self.r.close)

    def test_usage_phrases_resolve_fields_without_changing_source_name(self):
        for suffix in ('能派上什么用场？', '一般拿来干什么？', '的传统用途记在哪里？',
                       '主要针对哪些不适？', '有哪些应用记载？', '可以起什么效果？',
                       '有什么用途？', '的用途是什么？', '通常用来干什么呢？'):
            for method in ('name_field', 'name_bm25'):
                with self.subTest(suffix=suffix, method=method):
                    result = self.r.query('示例叶炭' + suffix, method=method)
                    self.assertEqual(result['status'], 'candidates')
                    self.assertEqual([(h['name'], h['label']) for h in result['hits']],
                                     [('示例叶炭', '功能与主治')])
                    self.assertTrue(any(v.get('rule') == 'bounded_usage_paraphrase'
                                        for v in result['query_interpretation']['field_matches']))

    def test_entity_guard_still_rejects_unknown_nouns_and_parent_name_substitution(self):
        for question, status in (
            ('外星草一般拿来干什么？', 'name_not_resolved'),
            ('酒示例叶一般拿来干什么？', 'variant_scope_unresolved'),
            ('示例叶提取物一般拿来干什么？', 'name_not_resolved'),
            ('示例叶炭片一般拿来干什么？', 'name_not_resolved'),
        ):
            result = self.r.query(question)
            self.assertEqual(result['status'], status)
            self.assertEqual(result['hits'], [])
        result = self.r.query('示例叶和示例叶炭一般拿来干什么？')
        self.assertIn(result['status'], ('multiple_names', 'name_not_resolved'))
        self.assertEqual(result['hits'], [])

    def test_full_question_grammar_does_not_accept_arbitrary_effect_or_personal_requests(self):
        for question in ('示例叶炭的市场价格', '示例叶炭的应用厂家', '示例叶炭的使用效果评分',
                         '示例叶炭能派上什么用场，今天卖多少钱',
                         '示例叶炭主要针对哪些不适，给我开处方',
                         '示例叶炭可以起什么效果，能替代我的药吗',
                         '我怀孕了，示例叶炭一般拿来干什么'):
            self.assertIsNone(usage_paraphrase(question), question)
        # Syntax alone is not identity approval: this looks like a use
        # question for an unknown longer noun, never for its parent herb.
        result = self.r.query('示例叶炭的测试用途')
        self.assertEqual(result['status'], 'field_not_resolved')
        self.assertEqual(result['hits'], [])

    def test_bounded_natural_route_preserves_question_and_explicit_sources(self):
        with patch.dict(os.environ, {'ENABLE_NATURAL_ROUTING': '1'}):
            for question in ('金银花能派上什么用场？', '连翘一般拿来干什么？',
                             '桔梗的传统用途记在哪里？', '五味子主要针对哪些不适？',
                             '丹参有哪些应用记载？', '麦冬可以起什么效果？',
                             '示例叶炭的用途是什么？'):
                result = resolve(question)
                self.assertEqual(result['tool'], 'research')
                self.assertEqual(result['reason'], 'pharmacopoeia_usage_paraphrase')
                self.assertEqual(result['request']['question'], question)
            self.assertEqual(resolve('在ETCM查询白术的用途')['tool'], 'herb')
            self.assertEqual(resolve('/research 白术一般拿来干什么')['mode'], 'explicit')
            self.assertEqual(resolve('什么是归经')['request']['topic'], 'concept')
        with patch.dict(os.environ, {'ENABLE_NATURAL_ROUTING': '0'}):
            self.assertIsNone(resolve('金银花能派上什么用场？'))

    def test_existing_canonical_multifield_and_missing_field_are_unchanged(self):
        result = self.r.query('示例叶炭的功效和用量')
        self.assertEqual({h['label'] for h in result['hits']}, {'功能与主治', '用法与用量'})
        self.assertEqual(self.r.query('示例叶的用量')['status'], 'ambiguous_field')
        self.assertEqual(self.r.query('示例叶炭的注意')['status'], 'field_not_observed')


if __name__ == '__main__':
    unittest.main()
