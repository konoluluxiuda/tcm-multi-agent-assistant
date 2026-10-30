"""Independent requirement checks must reject superficially complete reports."""
import copy
import unittest

from scripts.check_pair_architecture_preflight import CASES, assess


def report():
    side = {'text': {'hits': [{'label': label, 'evidence': [{'block_id': 'synthetic'}]}
                              for label in ('功能与主治', '注意')]},
            'structured': {'source_record_id': 'synthetic-source'}}
    item = {'sources': [{'witnesses': [{'source_pointer': '/x'}]} for _ in range(2)]}
    return {'status': 'complete', 'source_reports': [copy.deepcopy(side), copy.deepcopy(side)],
            'targets': {'status': 'ok', 'items': [copy.deepcopy(item) for _ in range(20)], 'total': 23}}


class AcceptanceTests(unittest.TestCase):
    def test_first_page_and_all_have_different_completion_requirements(self):
        self.assertTrue(assess(report(), CASES[0])['business_complete'])
        result = assess(report(), CASES[1])
        self.assertFalse(result['business_complete'])
        self.assertTrue(result['false_complete'])

    def test_missing_requested_field_fails_even_when_status_is_complete(self):
        data = report()
        data['source_reports'][1]['text']['hits'].pop()
        self.assertFalse(assess(data, CASES[0])['business_complete'])

    def test_missing_side_or_witness_cannot_count_as_completion(self):
        for field in ('source', 'witness'):
            data = report()
            if field == 'source':
                data['source_reports'][1]['structured'] = {}
            else:
                data['targets']['items'][0]['sources'][1]['witnesses'] = []
            self.assertFalse(assess(data, CASES[0])['business_complete'])

    def test_complete_target_coverage_can_pass(self):
        data = report()
        data['targets']['total'] = 20
        self.assertTrue(assess(data, CASES[1])['business_complete'])


if __name__ == '__main__':
    unittest.main()
