"""Recall denominators and frozen references must not conceal retrieval failures."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_mineru_retrieval import build
from scripts.eval_scoped_recall import evaluate, prepare, references, score_ids
from tcm.mineru_pharmacopoeia import digest
from tests.test_mineru_retrieval import fixture


class ScopedRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture(self.root / 'source')
        self.view_path = self.root / 'source/research_view.json'
        self.view = json.loads(self.view_path.read_text())
        self.suite = {'k': 1, 'cases': [
            {'id': 'positive', 'group': 'repeated_scope', 'question': '示例叶的用量',
             'name': '示例叶', 'labels': ['用法与用量']},
            {'id': 'boundary', 'group': 'boundary', 'question': '示例叶的市场价格',
             'allowed_statuses': ['field_not_resolved']},
        ]}
        self.questions = self.root / 'questions.json'
        self.questions.write_text(json.dumps(self.suite, ensure_ascii=False))

    def prepare(self):
        build(self.root / 'source', self.root / 'index')
        return prepare(self.questions, self.view_path, self.root / 'index', self.root / 'prepared')

    def test_complete_denominator_retains_duplicates_but_excludes_appendices(self):
        entry = self.view['entries'][0]
        entry['fields'].append({**entry['fields'][0], 'scope': 'appendix:test'})
        ref = references(self.suite, self.view)[0]
        self.assertEqual(ref['relevant_ids'], ['test-0:field:000', 'test-0:field:002'])
        score = score_ids(['test-0:field:000'], ref['relevant_ids'], 1)
        self.assertTrue(score['hit_at_k'])
        self.assertEqual(score['recall_at_k'], .5)

    def test_wrong_entity_and_duplicates_cannot_inflate_metrics(self):
        row = score_ids(['wrong', 'right'], ['right', 'second'], 1)
        self.assertEqual(row['recall_at_k'], 0)
        self.assertEqual(row['reciprocal_rank_at_k'], 0)
        with self.assertRaises(ValueError):
            score_ids(['right', 'right'], ['right', 'second'], 2)
        with self.assertRaises(ValueError):
            score_ids([], [], 5)

    def test_missing_or_ambiguous_reference_name_is_not_silently_skipped(self):
        self.suite['cases'][0]['name'] = '不存在'
        with self.assertRaises(ValueError):
            references(self.suite, self.view)
        self.suite['cases'][0]['name'] = '示例叶'
        self.view['entries'].append(self.view['entries'][0])
        with self.assertRaises(ValueError):
            references(self.suite, self.view)

    def test_reference_only_field_remains_in_denominator(self):
        field = self.view['entries'][0]['fields'][0]
        field.update(text='【用法与用量】同示例根。', raw_text='【用法与用量】同示例根。')
        ref = references(self.suite, self.view)[0]
        self.assertEqual(len(ref['relevant_ids']), 2)
        self.assertTrue(ref['locations'][0]['reference_or_empty_body'])

    def test_real_retriever_partial_recall_and_boundary_denominators(self):
        self.prepare()
        report = evaluate(self.root / 'prepared', self.view_path, self.root / 'index',
                          self.root / 'run', ('name_field',))
        result = report['results']['name_field']
        self.assertEqual(result['retrieval_cases'], 1)
        self.assertEqual(result['boundary_cases'], 1)
        self.assertEqual(result['hit_at_k'], 1)
        self.assertEqual(result['macro_recall_at_k'], .5)
        self.assertEqual(result['correct_boundaries'], 1)
        self.assertEqual(result['provenance_failures'], 0)
        self.assertEqual(digest(self.root / 'index/research.sqlite3'), report['manifest']['index_sha256'])
        with self.assertRaises(FileExistsError):
            evaluate(self.root / 'prepared', self.view_path, self.root / 'index', self.root / 'run')

    def test_source_or_reference_tamper_is_rejected(self):
        self.prepare()
        path = self.root / 'prepared/references.json'
        saved = json.loads(path.read_text())
        saved['references'][0]['relevant_ids'].pop()
        path.write_text(json.dumps(saved))
        with self.assertRaises(ValueError):
            evaluate(self.root / 'prepared', self.view_path, self.root / 'index', self.root / 'run')
        # Even updating the digest cannot turn an incomplete denominator into a valid reference.
        manifest_path = self.root / 'prepared/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['references_sha256'] = digest(path)
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'exhaust'):
            evaluate(self.root / 'prepared', self.view_path, self.root / 'index', self.root / 'run')

    def test_changed_view_requires_new_preparation(self):
        self.prepare()
        self.view_path.write_bytes(self.view_path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'view changed'):
            evaluate(self.root / 'prepared', self.view_path, self.root / 'index', self.root / 'run')


if __name__ == '__main__':
    unittest.main()
