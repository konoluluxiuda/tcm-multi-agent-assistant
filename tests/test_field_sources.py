"""Default-source behavior, explicit overrides and bounded model correction."""
import unittest
from copy import deepcopy

from tcm.dataops.field_sources import parse_fields
from tcm.dataops.preset_requests import parse
from tcm.dataops.requirements_understanding import _once, GoalResponseRejected
from tests.test_requirements_understanding import Sequence


class FieldSourceTests(unittest.TestCase):
    def test_default_combinations(self):
        for body, expected in [
            ('性味和剂量', [('etcm', 'properties'), ('etcm', 'flavors'), ('pharmacopoeia', '用法与用量')]),
            ('归经、科名、药用部位', [('etcm', 'meridians'), ('etcm', 'family'), ('etcm', 'medicinal_part')]),
            ('作用和注意事项及贮藏', [('pharmacopoeia', '功能与主治'), ('pharmacopoeia', '注意'), ('pharmacopoeia', '贮藏')]),
            ('性味与归经', [('etcm', 'properties'), ('etcm', 'flavors'), ('etcm', 'meridians')]),
        ]:
            with self.subTest(body=body):
                self.assertEqual([(f['namespace'], f['name']) for f in parse_fields(body)], expected)

    def test_explicit_source_wins_and_combined_document_field_is_read_once(self):
        self.assertEqual(parse_fields('药典性味和归经和剂量'), [
            {'namespace': 'pharmacopoeia', 'name': '性味与归经'},
            {'namespace': 'pharmacopoeia', 'name': '用法与用量'}])
        self.assertEqual(parse_fields('ETCM性味和药典性味'), [
            {'namespace': 'etcm', 'name': 'properties'}, {'namespace': 'etcm', 'name': 'flavors'},
            {'namespace': 'pharmacopoeia', 'name': '性味与归经'}])
        self.assertEqual(parse_fields('药典来源'), [{'namespace': 'pharmacopoeia', 'name': '来源'}])

    def test_unsupported_or_ambiguous_request_is_not_partially_consumed(self):
        for body in ('ETCM剂量', '药典科名', 'ETCM性味和剂量', '来源', '相关信息',
                     '性味和', '性味并推荐处方', '性味，不要归经', '性味和未知字段'):
            with self.subTest(body=body):
                self.assertIsNone(parse_fields(body))

    def test_multi_object_table_preserves_only_requested_fields(self):
        draft = parse('把白术和黄芪的性味和剂量生成表格')
        self.assertEqual(draft.delivery.form, 'table')
        self.assertEqual([o.mention for o in draft.objects], ['白术', '黄芪'])
        self.assertEqual([f.name for f in draft.requirements[0].fields], ['properties', 'flavors', '用法与用量'])

    def test_field_contract_uses_same_defaults(self):
        from tcm.question_contract import field_tasks
        tasks = field_tasks('白术的性味和剂量')
        self.assertEqual([(t['tool'], t['arguments']['fields']) for t in tasks], [
            ('herb_attributes', ['properties', 'flavors']), ('pharmacopoeia_lookup', ['用法与用量'])])

    def test_model_wrong_source_rejected_before_execution(self):
        question = '查询白术的性味'
        good = parse(question).model_dump()
        bad = deepcopy(good)
        bad['requirements'][0]['fields'] = [{'namespace': 'pharmacopoeia', 'name': '性味与归经'}]
        with self.assertRaises(GoalResponseRejected) as caught:
            _once(Sequence(bad), question, {'白术': ['s1']}, authorized=True)
        self.assertEqual(caught.exception.code, 'source_field_policy_mismatch')
        actual, _ = _once(Sequence(good), question, {'白术': ['s1']}, authorized=True)
        self.assertEqual(actual.model_dump(), good)

    def test_explicit_source_and_inherited_question_model_checks(self):
        question = '查询白术的药典性味和剂量'
        good = parse(question).model_dump()
        actual, _ = _once(Sequence(good), '给出药典性味和剂量', {'白术': ['s1']}, authorized=True,
            context={'conversation_scope': {'inherited': True, 'resolved_question': question}})
        self.assertEqual(actual.model_dump(), good)
        wrong = parse('查询白术的性味和剂量').model_dump()
        with self.assertRaises(GoalResponseRejected):
            _once(Sequence(wrong), question, {'白术': ['s1']}, authorized=True)

    def test_feedback_corrects_source_within_existing_budget(self):
        from tcm.dataops.requirements_feedback import run_requirements_feedback
        question = '查询白术的性味'
        good = parse(question).model_dump()
        wrong = parse('查询白术的药典性味').model_dump()
        model = Sequence(wrong, good, good)
        result = run_requirements_feedback(model, question, {'白术': ['s1']}, authorized=True)
        self.assertEqual(result['status'], 'ready', result)
        self.assertEqual([r['request']['field'] for r in result['query_routes']], ['properties', 'flavors'])
        self.assertEqual(model.calls, 3)
        self.assertEqual(result['external_model_calls'], 3)

    def test_failed_source_correction_stops_without_execution(self):
        from tcm.dataops.requirements_feedback import run_requirements_feedback
        wrong = parse('查询白术的药典性味').model_dump()
        model = Sequence(wrong, wrong)
        result = run_requirements_feedback(model, '查询白术的性味', {'白术': ['s1']}, authorized=True)
        self.assertEqual(result['status'], 'needs_input')
        self.assertEqual(result['failure']['code'], 'correction_exhausted')
        self.assertNotIn('query_routes', result)
        self.assertEqual(model.calls, 2)


if __name__ == '__main__':
    unittest.main()
