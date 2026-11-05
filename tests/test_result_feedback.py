import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from langchain_core.messages import AIMessage
from tests import test_requirements_feedback_http as fixtures
from tests.test_requirements_v3_http import RequirementsSequence
from tests.test_answer_delivery import output
from tcm.dataops.answer_delivery import merge_answer_datas
from tcm.dataops.result_feedback import review, observations, attach


class ReviewModel(RequirementsSequence):
    def invoke(self,messages):
        self.calls+=1
        self.payload=json.loads(messages[-1]['content'])
        item=self.replies.pop(0)
        if isinstance(item,Exception):raise item
        return AIMessage(content='',tool_calls=[{'id':'r','name':'review_query_result','args':item}],
                         usage_metadata={'input_tokens':10,'output_tokens':2,'total_tokens':12})


class ResultFeedbackTests(unittest.TestCase):
    def test_missing_values_cannot_be_promoted_and_private_data_not_sent(self):
        answer=merge_answer_datas([output('partial_evidence','PRIVATE_SOURCE_TEXT')],[{'tool':'herb'}])
        with tempfile.TemporaryDirectory() as tmp,patch('tcm.dataops.research_model.configured_model',
                return_value=ReviewModel({'action':'finish','step_ids':[]})) as factory:
            result=review(root=tmp,owner='o',key='k',proposal={'question':'字段'},answer=answer)
            self.assertEqual(result['status'],'rejected')
            self.assertNotIn('PRIVATE_SOURCE_TEXT',json.dumps(factory.return_value.payload))
            again=review(root=tmp,owner='o',key='k',proposal={'question':'字段'},answer=answer)
            self.assertEqual(again['requests_this_submission'],0)
            factory.assert_called_once_with(max_requests=1)

    def test_process_interruption_leaves_reserved_dispatch_and_never_repeats(self):
        answer=merge_answer_datas([output()],[{'tool':'herb'}])
        model=ReviewModel(KeyboardInterrupt())
        with tempfile.TemporaryDirectory() as tmp,patch('tcm.dataops.research_model.configured_model',return_value=model) as factory:
            with patch.object(model,'invoke',side_effect=KeyboardInterrupt()):
                with self.assertRaises(KeyboardInterrupt):
                    review(root=tmp,owner='o',key='k',proposal={'question':'字段'},answer=answer)
            result=review(root=tmp,owner='o',key='k',proposal={'question':'字段'},answer=answer)
            self.assertEqual(result['status'],'unknown')
            self.assertEqual(result['requests_this_submission'],0)
            self.assertEqual(factory.call_count,1)

    def test_clarification_downgrades_completion_but_keeps_tool_evidence(self):
        answer=merge_answer_datas([output()],[{'tool':'herb'}])
        out=attach(answer,{'status':'reviewed','action':'clarify','step_ids':['s1']})
        self.assertEqual(out['execution_status'],'success')
        self.assertEqual(out['status'],'partial')
        self.assertEqual(out['completion_status'],'needs_input')
        self.assertEqual(out['answer_steps'],answer['answer_steps'])
        self.assertEqual(answer['status'],'success')

    def test_document_candidates_include_presence_and_evidence_without_corpus_text(self):
        data={'research_result':{'status':'candidates','focus_name':'甲草',
              'tool_result':{'fields':['性味','性味与归经'],
                'request_interpretation':{'fields':['性味与归经']},'hits':[{'label':'性味与归经',
                'raw_text':'PRIVATE_DOCUMENT','evidence':[{'path':'PRIVATE_PATH'}]}]}}}
        answer=merge_answer_datas([data],[{'tool':'research','request':{'question':'甲草的性味与归经'}}])
        states=observations(answer)
        self.assertEqual(states[0]['object'],'甲草')
        self.assertEqual([f['field'] for f in states[0]['field_states']],['性味与归经'])
        self.assertEqual(states[0]['status_meaning'],'document_excerpts_returned_not_identity_verified')
        self.assertTrue(states[0]['field_states'][0]['has_value'])
        self.assertTrue(states[0]['field_states'][0]['evidence_present'])
        self.assertNotIn('PRIVATE_',json.dumps(states))
        data['research_result']['tool_result']['hits']=[]
        answer=merge_answer_datas([data],[{'tool':'research','request':{'question':'甲草的性味与归经'}}])
        with tempfile.TemporaryDirectory() as tmp,patch('tcm.dataops.research_model.configured_model',
                return_value=ReviewModel({'action':'finish','step_ids':[]})):
            self.assertEqual(review(root=tmp,owner='o',key='k',proposal={'question':'性味'},answer=answer)['status'],'rejected')

    def test_timeout_retains_unknown_without_redispatch(self):
        answer=merge_answer_datas([output()],[{'tool':'herb'}])
        with tempfile.TemporaryDirectory() as tmp,patch('tcm.dataops.research_model.configured_model',
                return_value=ReviewModel(TimeoutError())) as factory:
            result=review(root=tmp,owner='o',key='k',proposal={'question':'字段'},answer=answer)
            self.assertEqual(result['status'],'unknown')
            self.assertFalse(result['usage_known'])
            result=review(root=tmp,owner='o',key='k',proposal={'question':'字段'},answer=answer)
            self.assertEqual(result['requests_this_submission'],0)
            self.assertEqual(factory.return_value.calls,1)


class ResultFeedbackHTTPTests(unittest.TestCase):
    setUp=fixtures.FeedbackHTTPTests.setUp
    wait=fixtures.FeedbackHTTPTests.wait

    def test_execution_then_review_persists_and_replays_without_queries(self):
        import app
        models=[RequirementsSequence(fixtures.draft(),fixtures.draft()),
                ReviewModel({'action':'finish','step_ids':[]})]
        body={'query':'分别告诉我Alpha和Beta的药用部位','request_key':'post-review'}
        with patch.dict('os.environ',{'ENABLE_DATAOPS_RESULT_FEEDBACK':'1'}), \
             patch('tcm.dataops.research_model.configured_model',side_effect=models) as factory:
            first=self.client.post('/chat',json=body)
            self.assertEqual(first.status_code,200,first.text)
            data=first.json()
            self.assertEqual(data['result_feedback']['status'],'reviewed')
            self.assertEqual(data['model_calls'],3)
            self.assertEqual(data['result_feedback_requests_this_submission'],1)
            self.assertEqual(len(models[1].payload['tool_results']),2)
            with patch.object(app,'process_query',side_effect=AssertionError('No tool replay')):
                again=self.client.post('/chat',json=body)
            self.assertEqual(again.status_code,200,again.text)
            self.assertEqual(again.json()['model_calls'],0)
            self.assertEqual(again.json()['original_provider_requests'],3)
            self.assertEqual(again.json()['response'],data['response'])
            self.assertEqual(factory.call_count,2)
            saved=self.client.get('/conversation').json()['answer_delivery']
            self.assertEqual(saved['result_feedback']['action'],'finish')

    def test_failed_tool_is_preserved_and_model_requests_clarification(self):
        import app
        models=[RequirementsSequence(fixtures.draft(),fixtures.draft()),
                ReviewModel({'action':'clarify','step_ids':['s2']})]
        with patch.dict('os.environ',{'ENABLE_DATAOPS_RESULT_FEEDBACK':'1'}), \
             patch('tcm.dataops.research_model.configured_model',side_effect=models), \
             patch.object(app,'process_query',side_effect=[output(),output('clarification_needed')]) as process:
            r=self.client.post('/chat',json={'query':'Alpha和Beta的药用部位','request_key':'post-clarify'})
        self.assertEqual(r.status_code,200,r.text)
        b=r.json();self.assertEqual(b['status'],'partial')
        self.assertEqual(b['result_feedback']['action'],'clarify')
        self.assertEqual(len(b['answer_steps']),2)
        self.assertEqual(process.call_count,2)
        self.assertIn('Beta',b['response'])
