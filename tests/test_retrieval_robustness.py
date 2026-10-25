import unittest
from scripts.eval_retrieval_robustness import assess


class RobustnessScoringTests(unittest.TestCase):
    def setUp(self):
        self.hits = [{'chunk_id': i, 'name': '当归', 'text': i,
                      'raw_text': i, 'evidence': [{'page': 1}]} for i in ('a', 'b')]
        self.source = {h['chunk_id']: h for h in self.hits}
        self.case = {'herb': '当归', 'expected_chunk_ids': ['a', 'b'],
                     'primary_chunk_id': 'b', 'expect_abstention': False}

    def score(self, hits, status='candidates', case=None):
        return assess(case or self.case, {'hits': hits, 'status': status}, self.source)

    def test_one_field_is_not_full_task_success(self):
        self.assertEqual(self.score(self.hits[:1])['failure'], 'missing_required_fields')
        self.assertEqual(self.score([])['failure'], 'no_candidates')

    def test_order_and_coverage_are_separate(self):
        result = self.score(self.hits)
        self.assertEqual(result['covered_targets'], 2)
        self.assertEqual(result['failure'], 'wrong_priority')
        self.assertTrue(self.score(self.hits[::-1])['passed'])

    def test_duplicate_results_do_not_inflate_coverage(self):
        result = self.score([self.hits[0]] * 5)
        self.assertEqual(result['covered_targets'], 1)
        self.assertFalse(result['passed'])

    def test_tampered_provenance_and_wrong_entity_fail(self):
        for changed in ({'evidence': []}, {'text': 'invented'}, {'name': '川芎'}, {'chunk_id': 'unknown'}):
            result = self.score([{**self.hits[1], **changed}, self.hits[0]])
            self.assertEqual(result['failure'], 'provenance_or_entity')

    def test_infrastructure_failure_is_not_correct_abstention(self):
        case = {**self.case, 'expected_chunk_ids': [], 'primary_chunk_id': None, 'expect_abstention': True}
        self.assertTrue(self.score([], 'field_not_resolved', case)['passed'])
        for status in ('unavailable', 'timeout', 'name_not_resolved'):
            self.assertFalse(self.score([], status, case)['passed'])
        self.assertEqual(self.score(self.hits, case=case)['failure'], 'unexpected_answer')


if __name__ == '__main__':
    unittest.main()
