"""Single-source files, parent preservation and explicit clarification contracts."""
from copy import deepcopy
from io import BytesIO
import unittest
from unittest.mock import patch
from openpyxl import load_workbook
from pydantic import ValidationError
from tcm.dataops.contracts import Goal
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from tcm.dataops.requirements_understanding import correction_fields, submit_requirements_v2_tool
from tests import test_requirements_feedback_http as feedback_http
from tests.test_requirements_feedback_http import draft
from tests.test_requirements_v3_http import RequirementsSequence


class SingleSourceTests(unittest.TestCase):
    setUp = feedback_http.FeedbackHTTPTests.setUp
    wait = feedback_http.FeedbackHTTPTests.wait

    def create(self, value, key):
        with patch('tcm.dataops.research_model.configured_model', return_value=RequirementsSequence(value, value)):
            response = self.client.post('/api/dataops/research-questions', json={
                'question': '把Alpha和Beta按指定来源字段生成表格', 'request_key': key})
        self.assertEqual(response.status_code, 202, response.text)
        doc = self.wait(response.json()['workspace']['id'])
        self.assertEqual(doc['status'], 'complete', doc.get('failure'))
        return doc

    def test_etcm_file_append_document_preserves_parent(self):
        value = draft()
        value['delivery'] = {'form': 'table', 'row_policy': 'keep_all'}
        doc = self.create(value, 'single-source-parent')
        self.assertEqual(doc['goal']['document_fields'], [])
        self.assertTrue(all(r['mapping_status']=='not_requested' for r in doc['result']['rows']))
        self.assertTrue(all(e['kind']=='etcm' for e in doc['result']['evidence'].values()))
        url = '/api/dataops/workspaces/' + doc['id'] + '/artifact'
        original = self.client.get(url).content
        book = load_workbook(BytesIO(original), read_only=True)
        header = [c.value for c in next(book['结果'].rows)]
        book.close()
        self.assertIn('medicinal_part', header)
        self.assertNotIn('功能与主治', header)
        child = deepcopy(value)
        child['operation'] = 'append_fields'
        child['requirements'][0]['fields'] = [{'namespace':'pharmacopoeia','name':'功能与主治'}]
        with patch('tcm.dataops.research_model.configured_model',return_value=RequirementsSequence(child,child)):
            response = self.client.post('/api/dataops/research-questions', json={
                'question':'追加药典功能与主治，保留原字段','parent_id':doc['id'],
                'expected_revision':doc['revision'],'request_key':'single-source-child'})
        self.assertEqual(response.status_code,202,response.text)
        updated = self.wait(response.json()['workspace']['id'])
        self.assertEqual(updated['status'],'complete')
        self.assertEqual(updated['goal']['attributes'],['medicinal_part'])
        self.assertEqual(updated['goal']['document_fields'],['功能与主治'])
        self.assertEqual(self.client.get(url).content,original)
        self.assertTrue(any(e['kind']=='pharmacopoeia' for e in updated['result']['evidence'].values()))

    def test_document_only_has_no_default_etcm_column(self):
        value = draft()
        value['delivery'] = {'form':'table','row_policy':'keep_all'}
        value['requirements'][0]['fields'] = [{'namespace':'pharmacopoeia','name':'功能与主治'}]
        doc = self.create(value,'document-only-table')
        self.assertEqual(doc['goal']['attributes'],[])
        self.assertTrue(all(set(r['cells'])=={'功能与主治'} for r in doc['result']['rows']))

    def test_etcm_filter_does_not_require_pharmacopoeia_match(self):
        value = draft()
        value['delivery'] = {'form':'table','row_policy':'filter_complete'}
        with patch('tcm.dataops.data_access.Sources.inspect', side_effect=AssertionError('Unrequested pharmacopoeia inspection')):
            doc = self.create(value,'etcm-only-filter')
        self.assertEqual(doc['goal']['document_fields'],[])
        self.assertEqual(len(doc['result']['rows']),2)
        self.assertTrue(all(r['mapping_status']=='not_requested' for r in doc['result']['rows']))


class ClarificationContractTests(unittest.TestCase):
    def test_no_output_fields_rejected_but_legacy_defaults_preserved(self):
        with self.assertRaises(ValidationError):Goal(attributes=[],document_fields=[])
        self.assertTrue(Goal().attributes)
        self.assertTrue(Goal().document_fields)

    def test_model_schema_exposes_clarification_constraint(self):
        schema = submit_requirements_v2_tool()['function']['parameters']
        conditional = schema['allOf'][0]
        self.assertEqual(conditional['if']['properties']['status']['const'],'needs_input')
        self.assertEqual(conditional['then']['properties']['requirements']['maxItems'],0)
        self.assertEqual(conditional['then']['properties']['unresolved']['minItems'],1)

    def test_malformed_clarification_has_actionable_error_and_valid_stop(self):
        value = draft()
        value.update(status='needs_input',unresolved=['无法满足原字段，请明确需求'])
        with self.assertRaises(ValidationError) as caught:RequirementsDraftV2.model_validate(value)
        self.assertEqual(correction_fields(caught.exception)[0]['code'],'clarification_has_executable_requirements')
        value['requirements'] = []
        from tests.test_requirements_feedback import RecordingModel
        from tcm.dataops.requirements_feedback import run_requirements_feedback
        model = RecordingModel(value)
        result = run_requirements_feedback(model,'q',{'Alpha':['a'],'Beta':['b']},authorized=True)
        self.assertEqual(result['status'],'needs_input')
        self.assertNotIn('goal',result)
        self.assertEqual(model.calls,1)


from tests import test_dataops_d2 as d2


class LegacySingleSourceTests(unittest.TestCase):
    setUp = d2.D2Tests.setUp
    create = d2.D2Tests.create
    engine = d2.D2Tests.engine

    def test_legacy_single_source_does_not_ask_for_unrequested_mapping(self):
        doc = self.create(names=('Gamma',), fields=())
        with patch('tcm.dataops.data_access.Sources.inspect', side_effect=AssertionError('Unrequested mapping')):
            result = self.engine(doc).run()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['result']['rows'][0]['mapping_status'], 'not_requested')
        self.assertNotIn('ask_user', [e['tool'] for e in result['progress']['events']])
