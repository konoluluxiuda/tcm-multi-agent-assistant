"""Offline regressions for the first live run's delivery/feedback failures."""
import json
import unittest
from pydantic import ValidationError
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from tcm.dataops.requirements_understanding import request_requirements, correction_fields, submit_requirements_v2_tool
from tests.test_requirements_understanding import Capture, good_draft


class DeliveryFeedbackTests(unittest.TestCase):
    def test_model_schema_distinguishes_answer_and_table(self):
        schema=submit_requirements_v2_tool()['function']['parameters']
        self.assertEqual(schema['properties']['delivery']['discriminator']['propertyName'],'form')
        answer=schema['$defs']['AnswerDelivery']
        self.assertEqual(answer['properties']['row_policy']['type'],'null')
        self.assertEqual(answer['properties']['count']['type'],'null')
        self.assertIn('row_policy',schema['$defs']['TableDelivery']['required'])

    def test_recorded_failure_shape_gets_actionable_feedback(self):
        for count in (None,2):
            draft=good_draft();draft['delivery'].update(row_policy='keep_all',count=count)
            with self.assertRaises(ValidationError) as caught:RequirementsDraftV2.model_validate(draft)
            feedback=correction_fields(caught.exception)
            self.assertEqual(feedback[0]['code'],'answer_table_parameter_forbidden')
            self.assertIn('省略row_policy和count',feedback[0]['message'])
            serialized=json.dumps(feedback)
            self.assertNotIn('source_sha256',serialized)
            self.assertNotIn('location',serialized)

    def test_corrected_parameters_do_not_bypass_semantic_budget(self):
        bad=good_draft();bad['delivery']['row_policy']='keep_all'
        model=Capture(bad,good_draft())
        result=request_requirements(model,'黄芪的功效',{'黄芪':['source']},authorized=True)
        self.assertEqual(model.calls,2)
        self.assertEqual(result['failure']['code'],'semantic_review_budget_exhausted')
        self.assertIn('answer_table_parameter_forbidden',json.dumps(model.payloads,ensure_ascii=False))
        self.assertIn('available_fields',json.dumps(model.payloads,ensure_ascii=False))
        self.assertIn('medicinal_part',json.dumps(model.payloads,ensure_ascii=False))

    def test_valid_answer_still_completes_two_pass_review(self):
        model=Capture(good_draft(),good_draft())
        result=request_requirements(model,'黄芪的功效',{'黄芪':['source']},authorized=True)
        self.assertEqual(result['status'],'ready');self.assertEqual(model.calls,2)

    def test_old_serialized_null_delivery_still_readable(self):
        draft=good_draft();draft['delivery'].update(row_policy=None,count=None)
        value=RequirementsDraftV2.model_validate(draft)
        self.assertEqual(value,RequirementsDraftV2.model_validate(value.model_dump()))

    def test_top_n_and_append_still_fail_closed(self):
        for delivery in ({'form':'table','row_policy':'top_n'},
                         {'form':'table','row_policy':'keep_all','count':2}):
            draft=good_draft();draft['delivery']=delivery
            with self.assertRaises(ValidationError):RequirementsDraftV2.model_validate(draft)
        draft=good_draft();draft['operation']='append_fields'
        with self.assertRaises(ValidationError):RequirementsDraftV2.model_validate(draft)
