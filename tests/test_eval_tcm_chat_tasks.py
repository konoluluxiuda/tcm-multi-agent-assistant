"""Prevent false passes when chat provenance or the isolation setup is broken."""
import copy
import unittest

from scripts.eval_tcm_chat_tasks import check_isolated_session, check_same_session


class ChatTaskCheckerTests(unittest.TestCase):
    def setUp(self):
        self.source = {'relative_file_path': 'etcm_herbs/Fake.json', 'file_sha256': 'a'*64,
                       'json_pointer': '/data/0/value/3/value', 'raw_value': 'FakeFamily'}
        self.case = {'input': {'dataset_version': 'fixture'}, 'source_evidence': [{
            'relative_path': self.source['relative_file_path'], 'sha256': self.source['file_sha256'],
            'json_pointer': self.source['json_pointer'], 'raw_value': self.source['raw_value']}],
            'expected': {'future_target': {'source_record_id': 'fixture:source',
                                         'fields': {'family': {'state': 'present', 'value': 'FakeFamily'}},
                                         'forbidden_implicit_source_record_id': 'fixture:source'}}}
        record = {'source_record_id': 'fixture:source', 'fields': {
            'family': {'state': 'present', 'value': 'FakeFamily', 'source': self.source},
            'name_pinyin': {'state': 'present', 'value': 'Fake', 'source': self.source}}}
        self.turn = {'http_status': 200, 'body': {'agent': 'TCM_HERB_AGENT', 'tcm': {
            'status': 'ok', 'dataset_version': 'fixture', 'records': [record], 'followup': True}}}
        self.empty = {'http_status': 200, 'body': {'agent': 'TCM_HERB_AGENT',
            'response': '先 /herb 查询', 'tcm': {'status': 'clarify_entity', 'records': []}}}

    def test_matching_target_passes_but_wrong_provenance_fails(self):
        check_same_session(self.case, [self.turn, self.turn])
        for key in self.source:
            changed = copy.deepcopy(self.turn)
            changed['body']['tcm']['records'][0]['fields']['family']['source'][key] = 'wrong'
            with self.subTest(key=key), self.assertRaises(AssertionError):
                check_same_session(self.case, [self.turn, changed])

    def test_incorrect_entity_value_or_missing_turn_cannot_pass(self):
        for mutate in [lambda t: t.update(http_status=503),
                       lambda t: t['body']['tcm'].update(followup=False),
                       lambda t: t['body']['tcm']['records'][0].update(source_record_id='other'),
                       lambda t: t['body']['tcm']['records'][0]['fields']['family'].update(value='invented')]:
            changed = copy.deepcopy(self.turn)
            mutate(changed)
            with self.assertRaises(AssertionError):
                check_same_session(self.case, [self.turn, changed])
        with self.assertRaises(AssertionError):
            check_same_session(self.case, [self.turn])

    def test_isolation_requires_valid_setup_and_no_leaked_source(self):
        check_isolated_session(self.case, [self.turn, self.empty])
        changed = copy.deepcopy(self.turn)
        changed['http_status'] = 500
        with self.assertRaises(AssertionError):
            check_isolated_session(self.case, [changed, self.empty])
        leaked = copy.deepcopy(self.empty)
        leaked['body']['response'] += ' fixture:source'
        with self.assertRaises(AssertionError):
            check_isolated_session(self.case, [self.turn, leaked])
        with self.assertRaises(AssertionError):
            check_isolated_session(self.case, [self.turn, self.turn])
