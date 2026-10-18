"""Prevent inflated challenge scores from wrong evidence or scope substitution."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_mineru_retrieval import build
from scripts.eval_mineru_challenge import prepare, evaluate
from tcm.mineru_challenge import score
from tcm.mineru_pharmacopoeia import digest
from tests.test_mineru_retrieval import fixture


class ChallengeScoreTests(unittest.TestCase):
    def setUp(self):
        self.case = {'id': 'x', 'group': 'canonical'}
        self.reference = {'kind': 'corpus_proposed_evidence', 'name': '示例药',
                          'targets': [{'chunk_id': 'id:field:001', 'evidence': [{'source': 'original'}]}]}
        self.hit = {'chunk_id': 'id:field:001', 'name': '示例药', 'evidence': [{'source': 'original'}]}

    def result(self, hits, status='candidates'):
        return {'hits': hits, 'status': status}

    def test_matching_name_and_exact_evidence_required(self):
        self.assertTrue(score(self.case, self.reference, self.result([self.hit]))['target_top1'])
        for changed in [dict(self.hit, name='其他药'), dict(self.hit, evidence=[{'source': 'different'}]),
                        dict(self.hit, chunk_id='other')]:
            self.assertFalse(score(self.case, self.reference, self.result([changed]))['target_evidence_hit_at_5'])

    def test_rank_and_reciprocal_rank_include_wrong_first_hit(self):
        result = score(self.case, self.reference, self.result([dict(self.hit, chunk_id='other'), self.hit]))
        self.assertEqual(result['rank'], 2)
        self.assertEqual(result['reciprocal_rank'], .5)
        self.assertFalse(result['target_top1'])

    def test_partial_repeated_field_retrieval_is_not_complete(self):
        reference = deepcopy(self.reference)
        reference['targets'].append({'chunk_id': 'id:field:002', 'evidence': [{'source': 'second'}]})
        result = score(self.case, reference, self.result([self.hit]))
        self.assertTrue(result['target_evidence_hit_at_5'])
        self.assertFalse(result['returned_all_target_fields'])

    def test_scope_guard_rejects_unqualified_parent_substitution(self):
        reference = {'kind': 'variant_scope_guard'}
        result = score(self.case, reference, self.result([self.hit]))
        self.assertFalse(result['behavior_passed'])
        self.assertTrue(result['unqualified_parent_substitution'])
        self.assertTrue(score(self.case, reference, self.result([], 'name_not_resolved'))['behavior_passed'])
        self.assertTrue(score(self.case, reference, self.result([self.hit], 'ambiguous_field'))['behavior_passed'])

    def test_abstention_requires_status_and_no_hits(self):
        case = dict(self.case, group='out_of_scope', expected_status='multiple_names')
        self.assertTrue(score(case, None, self.result([], 'multiple_names'))['behavior_passed'])
        self.assertFalse(score(case, None, self.result([], 'no_match'))['behavior_passed'])
        self.assertFalse(score(case, None, self.result([self.hit], 'multiple_names'))['behavior_passed'])


class ChallengeIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        fixture(self.root / 'view'); receipt = build(self.root / 'view', self.root / 'index')
        self.questions = self.root / 'questions.json'
        self.questions.write_text(json.dumps({'k': 5, 'cases': [{'id': 'x', 'group': 'canonical',
            'question': '示例叶炭的功效？', 'expected_name': '示例叶炭', 'expected_field': '功能与主治'}]}))
        self.freeze = self.root / 'freeze.json'
        self.freeze.write_text(json.dumps({'question_sha256': digest(self.questions),
            'index_sha256': receipt['index_sha256'],
            'retriever_sha256': digest(Path(__file__).resolve().parents[1] / 'tcm/mineru_retrieval.py')}))
        self.refs = self.root / 'refs.json'
        prepare(self.questions, self.root / 'view/research_view.json', self.freeze, self.refs)

    def tearDown(self):
        self.temp.cleanup()

    def run_evaluation(self):
        return evaluate(self.questions, self.refs, self.freeze, self.root / 'index', self.root / 'output')

    def test_frozen_reference_drives_actual_index_evaluation(self):
        result = self.run_evaluation()
        self.assertEqual(result['groups']['name_field']['canonical']['target_hit_at_5_count'], 1)
        self.assertFalse(result['human_gold'])
        with self.assertRaises(FileExistsError):
            self.run_evaluation()

    def test_reference_change_is_rejected_without_output(self):
        self.refs.write_text('{}')
        with self.assertRaises(ValueError):
            self.run_evaluation()
        self.assertFalse((self.root / 'output').exists())

    def test_question_change_is_rejected_without_output(self):
        original = self.questions.read_text(); self.questions.write_text(original + ' ')
        with self.assertRaises(ValueError):
            self.run_evaluation()
        self.assertFalse((self.root / 'output').exists())
