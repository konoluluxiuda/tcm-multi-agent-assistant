"""Bounded feedback behavior: real compiler, controlled SDK and read-only sources."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from tcm.dataops.requirements_feedback import run_requirements_feedback as run, probe_availability
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from tcm.dataops.data_access import SourceChanged
from tests.test_requirements_pipeline import Sequence


def answer():
    return {'version': 'requirements-v2', 'status': 'ready', 'operation': 'new',
            'objects': [{'ref': 'o1', 'mention': 'Alpha'}],
            'requirements': [{'id': 'r1', 'kind': 'read_fields', 'object_refs': ['o1'],
                              'fields': [{'namespace': 'etcm', 'name': 'reference_pmids'}]}],
            'delivery': {'form': 'answer'}, 'unresolved': []}


def table(draft):
    result = deepcopy(draft)
    result['delivery'] = {'form': 'table', 'row_policy': 'keep_all'}
    return result


class RecordingModel(Sequence):
    def __init__(self, *replies):
        super().__init__(*replies)
        self.messages = []

    def invoke(self, messages):
        self.messages.append(deepcopy(messages))
        return super().invoke(messages)


class FeedbackTests(unittest.TestCase):
    catalog = {'Alpha': ['private-source-id']}

    def run_model(self, model, **kwargs):
        return run(model, '请回答Alpha的参考文献编号', self.catalog, authorized=True, **kwargs)

    def test_success_preflight_between_extraction_and_review(self):
        draft = answer()
        events = []
        model = RecordingModel(draft, draft)
        original = model.invoke
        def invoke(messages):
            events.append('model')
            return original(messages)
        model.invoke = invoke
        from tcm.dataops.requirements_feedback import preflight
        def check(*args, **kwargs):
            events.append('preflight')
            return preflight(*args, **kwargs)
        with patch('tcm.dataops.requirements_feedback.preflight', side_effect=check):
            result = self.run_model(model)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(events, ['model', 'preflight', 'model'])
        self.assertEqual(result['external_model_calls'], 2)
        self.assertEqual(result['usage']['total_tokens'], 24)
        self.assertIn('query_routes', result)
        self.assertNotIn('goal', result)

    def test_actual_tool_form_error_repaired_then_reviewed(self):
        draft = answer()
        model = RecordingModel(table(draft), draft, draft)
        result = self.run_model(model)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['understanding_attempts'], ['initial', 'tool_correction', 'semantic_review'])
        payload = json.loads(model.messages[1][1]['content'])
        errors = payload['validation_feedback']['checks']
        mismatch = next(e for e in errors if e['code'] == 'field_delivery_unsupported')
        self.assertEqual(mismatch['allowed_forms'], ['answer'])
        self.assertEqual(mismatch['name'], 'reference_pmids')
        self.assertIn('previous_requirements', payload['validation_feedback'])
        review = json.loads(model.messages[2][1]['content'])
        self.assertNotIn('validation_feedback', review)
        self.assertNotIn('private-source-id', json.dumps(model.messages))
        self.assertEqual(result['external_model_calls'], 3)

    def test_user_requested_table_is_not_silently_downgraded(self):
        draft = answer()
        model = RecordingModel(table(draft), draft, table(draft))
        result = run(model, '把Alpha的参考文献编号导出表格', self.catalog, authorized=True)
        self.assertEqual(result['failure']['code'], 'semantic_disagreement')
        self.assertNotIn('query_routes', result)

    def test_schema_repair_retains_budget_for_review(self):
        bad = answer()
        bad['delivery']['row_policy'] = 'keep_all'
        model = RecordingModel(bad, answer(), answer())
        result = self.run_model(model)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['understanding_attempts'], ['initial', 'correction', 'semantic_review'])
        self.assertEqual(result['usage']['total_tokens'], 36)

    def test_schema_and_tool_errors_share_one_repair(self):
        bad = answer()
        bad['delivery']['count'] = 1
        model = RecordingModel(bad, table(answer()))
        result = self.run_model(model)
        self.assertEqual(model.calls, 2)
        self.assertEqual(result['failure']['code'], 'feedback_repair_exhausted')

    def test_repeated_tool_error_does_not_loop(self):
        model = RecordingModel(table(answer()), table(answer()))
        result = self.run_model(model)
        self.assertEqual(model.calls, 2)
        self.assertEqual(result['failure']['code'], 'feedback_repair_exhausted')

    def test_repair_drops_field_review_blocks_delivery(self):
        original = answer()
        original['requirements'][0]['fields'].append({'namespace': 'etcm', 'name': 'family'})
        model = RecordingModel(table(original), answer(), original)
        result = self.run_model(model)
        self.assertEqual(result['failure']['code'], 'semantic_disagreement')
        self.assertNotIn('query_routes', result)

    def test_ambiguous_or_missing_name_not_guessed(self):
        for candidates in ([], ['a', 'b']):
            with self.subTest(candidates=candidates):
                model = RecordingModel(answer())
                result = run(model, 'Alpha参考文献', {'Alpha': candidates}, authorized=True)
                self.assertEqual(model.calls, 1)
                self.assertIn(result['failure']['code'], ('source_not_found', 'source_ambiguous'))

    def test_all_relations_not_downgraded_to_one_page(self):
        draft = answer()
        draft['requirements'] = [{'id': 'r1', 'kind': 'relation', 'object_refs': ['o1'],
                                  'relation_kind': 'ingredient', 'completeness': 'all'}]
        model = RecordingModel(draft)
        result = self.run_model(model)
        self.assertEqual(result['failure']['code'], 'full_relation_read_unsupported')
        self.assertEqual(model.calls, 1)

    def test_unknown_dispatch_in_each_phase_never_retried(self):
        variants = [[TimeoutError('unknown')],
                    [table(answer()), TimeoutError('unknown')],
                    [table(answer()), answer(), TimeoutError('unknown')]]
        for replies in variants:
            with self.subTest(phase=len(replies)):
                model = RecordingModel(*replies)
                with self.assertRaises(TimeoutError):
                    self.run_model(model)
                self.assertEqual(model.calls, len(replies))

    def test_optout_never_calls_model(self):
        model = RecordingModel(answer())
        with self.assertRaises(PermissionError):
            run(model, 'Alpha', self.catalog)
        self.assertEqual(model.calls, 0)

    def test_invalid_tool_no_extra_calls(self):
        model = RecordingModel(('other_tool', {}))
        result = self.run_model(model)
        self.assertEqual(result['failure']['code'], 'invalid_requirements_call')
        self.assertEqual(model.calls, 1)

    def test_adapter_cannot_inject_default_columns(self):
        draft = answer()
        draft['requirements'][0]['fields'] = [{'namespace': 'etcm', 'name': 'family'}]
        model = RecordingModel(table(draft), table(draft))
        result = self.run_model(model)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['goal']['attributes'], ['family'])
        self.assertEqual(result['goal']['document_fields'], [])
        self.assertEqual(model.calls, 2)

    def test_source_change_stops_before_next_model_request(self):
        class Sources:
            def query(self, spec):
                raise SourceChanged('private-path-and-source')
        model = RecordingModel(answer())
        with self.assertRaises(SourceChanged):
            self.run_model(model, sources=Sources())
        self.assertEqual(model.calls, 1)

    def test_read_probe_is_bounded_and_does_not_send_values(self):
        draft = answer()
        draft['objects'] = [{'ref': f'o{i}', 'mention': f'Name{i}'} for i in range(1, 4)]
        draft['requirements'][0].update(object_refs=['o1', 'o2', 'o3'], fields=[
            {'namespace': 'etcm', 'name': field} for field in
            ('family', 'habitat', 'latin_name', 'medicinal_part', 'properties')])
        class Sources:
            calls = 0
            def query(self, spec):
                self.calls += 1
                return {'records': [{'source_record_id': spec['source_ids'][0],
                    'fields': {'family': {'state': 'present', 'value': 'PRIVATE CORPUS VALUE'},
                               'habitat': {'state': 'missing', 'value': None}}}]}
            def assert_unchanged(self):
                pass
        sources = Sources()
        observed = probe_availability(RequirementsDraftV2.model_validate(draft),
                                      {f'Name{i}': [f'secret-id{i}'] for i in range(1, 4)}, sources)
        self.assertEqual(sources.calls, 2)
        self.assertEqual(len(observed['cells']), 8)
        self.assertFalse(observed['complete'])
        self.assertNotIn('PRIVATE', json.dumps(observed))
        self.assertNotIn('secret-id', json.dumps(observed))
        self.assertIn('missing', {c['availability'] for c in observed['cells']})

    def test_missing_fields_are_observations_not_deleted_requirements(self):
        class Sources:
            def query(self, spec):
                return {'records': [{'source_record_id': spec['source_ids'][0],
                                     'fields': {'reference_pmids': {'state': 'missing', 'value': None}}}]}
            def assert_unchanged(self):
                pass
        model = RecordingModel(answer(), answer())
        result = self.run_model(model, sources=Sources())
        self.assertEqual(result['status'], 'ready')
        payload = json.loads(model.messages[1][1]['content'])
        self.assertEqual(payload['tool_observations']['availability']['cells'][0]['availability'], 'missing')
        self.assertEqual(result['query_routes'][0]['request']['field'], 'reference_pmids')

    def test_parent_scope_error_does_not_dispatch_repair(self):
        model = RecordingModel(answer())
        result = self.run_model(model, parent={'goal': {'query': {'names': ['Alpha']}}})
        self.assertEqual(result['failure']['code'], 'parent_operation_conflict')
        self.assertEqual(model.calls, 1)

    def test_optin_policy_routes_plain_queries_through_feedback(self):
        from tcm.dataops.request_policy import needs_fallback
        from tcm.capabilities import research_runtime
        with patch.dict('os.environ', {'DATAOPS_UNDERSTANDING_POLICY': 'requirements_feedback_v1'}):
            self.assertTrue(needs_fallback('Alpha的科名', {'tool': 'herb', 'request': {'field': 'family'}}))
            self.assertFalse(needs_fallback('数据概览', None))
            self.assertEqual(research_runtime()['question_request_limit'], 3)

    def test_local_probe_error_has_no_exception_payload(self):
        from tcm.dataops.requirements_feedback import SourceProbeFailed
        class Sources:
            def query(self, spec):
                raise OSError('PRIVATE FILE PATH')
        model = RecordingModel(answer())
        with self.assertRaises(SourceProbeFailed) as caught:
            self.run_model(model, sources=Sources())
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(model.calls, 1)
