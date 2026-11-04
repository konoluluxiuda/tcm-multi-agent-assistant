"""Counterfactual requirements, bounded repair and actual HTTP orchestration.

SDK replies are controlled fixtures, not measurements of model accuracy.
"""
import json
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage
from tests.test_research_question import draft
from tests.test_unified_request_harness import Model
from tests import test_unified_request_harness as legacy
from tcm.dataops.research_question import request_proposal, local_proposal
from tcm.dataops.request_policy import needs_fallback


class Sequence(Model):
    def __init__(self, *replies):
        super().__init__({})
        self.replies=list(replies)

    def invoke(self, messages):
        self.calls+=1;self.messages.append(messages)
        if not self.replies:raise AssertionError('Unexpected third model request')
        reply=self.replies.pop(0)
        if isinstance(reply,Exception):raise reply
        tool,args=reply if isinstance(reply,tuple) else ('propose_research_goal',reply)
        return AIMessage(content='',tool_calls=[{'id':str(self.calls),'name':tool,'args':args}],
                         usage_metadata={'input_tokens':10,'output_tokens':2,'total_tokens':12})


class HarnessTests(unittest.TestCase):
    catalog={'Alpha':['a'],'Beta':['b']}
    plain='整理Alpha和Beta的功效和注意，生成表格'
    unfamiliar='Alpha、Beta中，哪些同时有药典功效和注意记载？只把符合条件的整理成表。'

    def run_model(self, model, question=None, **kwargs):
        result=request_proposal(model,question or self.plain,self.catalog,authorized=True,
                                allow_queries=True,policy='unified_v2',**kwargs)
        self.assertLessEqual(model.calls,2)
        self.assertEqual(result['external_model_calls'],model.calls)
        self.assertEqual(result['usage']['total_tokens'],12*model.calls)
        self.assertNotIn('_draft',result);self.assertNotIn('_intent',result)
        return result

    def test_plain_audit_cannot_silently_become_complete_filter(self):
        model=Sequence(draft(mode='filter_complete'))
        result=self.run_model(model)
        self.assertEqual(result['failure']['code'],'semantic_disagreement')
        self.assertIsNone(result['goal']);self.assertEqual(model.calls,1)

    def test_unknown_filter_cannot_silently_become_keep_all(self):
        self.assertNotEqual(local_proposal(self.unfamiliar,self.catalog)['status'],'ready')
        model=Sequence(draft(),draft(mode='filter_complete'))
        result=self.run_model(model,self.unfamiliar)
        self.assertEqual(result['failure']['code'],'semantic_disagreement')
        self.assertEqual(result['understanding_attempts'],['initial','semantic_review'])
        # Review does not see the first proposal or its chosen row policy.
        payload=json.loads(model.messages[1][1]['content'])
        self.assertNotIn('validation_feedback',payload)
        self.assertNotIn('audit_named',json.dumps(payload))

    def test_unknown_correct_filter_is_accepted_after_independent_agreement(self):
        model=Sequence(draft(mode='filter_complete'),draft(mode='filter_complete'))
        result=self.run_model(model,self.unfamiliar)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['requirements']['row_policy'],'filter_complete')
        self.assertEqual(result['requirements_basis'],'independent_model_agreement')
        self.assertEqual(result['requirements']['output'],'table')

    def test_clear_local_contract_needs_only_one_call(self):
        result=self.run_model(Sequence(draft()))
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['requirements_basis'],'local_requirement_contract')
        self.assertEqual(result['external_model_calls'],1)

    def test_received_schema_error_can_be_corrected_once(self):
        model=Sequence(draft(mode='unsupported_mode'),draft())
        result=self.run_model(model)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['understanding_attempts'],['initial','correction'])
        self.assertEqual(json.loads(model.messages[1][1]['content'])['validation_feedback']['code'],
                         'schema_validation_failed')
        self.assertEqual(json.loads(model.messages[1][1]['content'])['validation_feedback']['fields'],
                         [{'path':['mode'],'type':'literal_error'}])

    def test_omitted_field_is_not_executed_and_can_be_corrected(self):
        result=self.run_model(Sequence(draft(document_fields=['功能与主治']),draft()))
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['external_model_calls'],2)

    def test_correction_cannot_consume_a_third_call_for_review(self):
        model=Sequence(draft(mode='invalid'),draft(mode='filter_complete'))
        result=self.run_model(model,self.unfamiliar)
        self.assertEqual(result['failure']['code'],'semantic_review_budget_exhausted')
        self.assertIsNone(result['goal'])

    def test_two_invalid_responses_stop(self):
        result=self.run_model(Sequence(draft(mode='invalid'),draft(mode='invalid')))
        self.assertEqual(result['failure']['code'],'correction_exhausted')

    def test_unknown_dispatch_never_attempts_correction(self):
        for replies,count in [([TimeoutError('unknown')],1),
                              ([draft(mode='filter_complete'),TimeoutError('unknown')],2)]:
            model=Sequence(*replies)
            with self.assertRaises(TimeoutError):self.run_model(model,self.unfamiliar)
            self.assertEqual(model.calls,count)

    def test_reviewer_invalid_or_uncertain_never_delivers(self):
        for second in (draft(mode='invalid'),draft(status='needs_input',issues=['请明确是否保留缺失项'])):
            result=self.run_model(Sequence(draft(mode='filter_complete'),second),self.unfamiliar)
            self.assertEqual(result['failure']['code'],'semantic_review_failed')

    def test_field_addition_disagreement_is_not_only_a_row_policy_check(self):
        q='请为Alpha和Beta编一份功效档案，留作资料表'
        self.assertNotEqual(local_proposal(q,self.catalog)['status'],'ready')
        result=self.run_model(Sequence(draft(document_fields=['功能与主治','注意']),
                                       draft(document_fields=['功能与主治'])),q)
        self.assertEqual(result['failure']['code'],'semantic_disagreement')

    def test_query_shape_error_is_correctable_without_extra_planner(self):
        model=Sequence(('submit_query_intent',{'action':'herb_field','entities':['Alpha'],'fields':['bad']}),
                       ('submit_query_intent',{'action':'herb_field','entities':['Alpha'],'fields':['medicinal_part']}))
        result=request_proposal(model,'想知道Alpha的药用部位',{'Alpha':['a']},authorized=True,
                                allow_queries=True,policy='unified_v2')
        self.assertEqual(result['status'],'query');self.assertEqual(result['external_model_calls'],2)
        self.assertEqual(result['requirements']['output'],'answer')

    def test_pharmacopoeia_question_cannot_be_routed_to_herb_field(self):
        # "作用" maps to 功能与主治 (pharmacopoeia), not an ETCM attribute dump.
        model=Sequence(('submit_query_intent',{'action':'herb_field','entities':['黄茉'],
                        'fields':['name_pinyin','latin_name','family']}))
        result=request_proposal(model,'黄茉有什么作用',{'黄茉':['a']},
                                authorized=True,allow_queries=True,policy='unified_v2')
        self.assertEqual(result['status'],'needs_input')
        self.assertEqual(result['failure']['code'],'query_routed_to_wrong_tool')
        self.assertEqual(result['external_model_calls'],1)
        self.assertEqual(model.calls,1)

    def test_wrong_old_route_cannot_bypass_unified_understanding(self):
        with patch.dict('os.environ',{'DATAOPS_UNDERSTANDING_POLICY':'unified_v2'}):
            for q in ('想知道连翘的药用部位','查看白术的药用部位、科名、性味、归经'):
                self.assertTrue(needs_fallback(q,{'request':{'topic':'herbs'},'reason':'looks_valid'}))
            for q in ('/herb 白术','你能做什么','数据概览'):
                self.assertFalse(needs_fallback(q,None))

    def test_four_user_attributes_expand_to_five_source_fields_without_dropping_flavors(self):
        fields=['medicinal_part','family','properties','flavors','meridians']
        model=Sequence(('submit_query_intent',{'action':'herb_field','entities':['白术'],'fields':[f for f in fields if f!='flavors']}),
                       ('submit_query_intent',{'action':'herb_field','entities':['白术'],'fields':fields}))
        result=request_proposal(model,'查看白术的药用部位、科名、性味、归经',{'白术':['a']},
                                authorized=True,allow_queries=True,policy='unified_v2')
        self.assertEqual(result['status'],'query')
        self.assertEqual(set(result['route']['request']['fields']),set(fields))
        self.assertEqual(result['external_model_calls'],2)


class HTTPTests(unittest.TestCase):
    wait=legacy.EntryTests.wait

    def setUp(self):
        legacy.EntryTests.setUp(self)
        p=patch.dict('os.environ',{'DATAOPS_UNDERSTANDING_POLICY':'unified_v2'})
        p.start();self.addCleanup(p.stop)

    def test_model_understands_even_when_local_parser_has_a_ready_task(self):
        model=Sequence(draft())
        body={'query':HarnessTests.plain,'request_key':'v2-local-understood'}
        with patch('tcm.dataops.research_model.configured_model',return_value=model) as configured:
            first=self.client.post('/chat',json=body)
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(first.json()['model_calls'],1)
            configured.assert_called_once_with(max_requests=2)
            doc=self.wait(first.json()['assistant']['workspace_id'])
            self.assertEqual(len(doc['result']['rows']),2)
            self.assertEqual(doc['goal_understanding']['requirements']['row_policy'],'keep_all')
            again=self.client.post('/chat',json=body)
            self.assertEqual(again.json()['model_calls'],0);self.assertEqual(model.calls,1)

    def test_two_call_disagreement_is_persisted_no_workspace_no_repeat_call(self):
        model=Sequence(draft(),draft(mode='filter_complete'))
        self.client.get('/dataops/research')
        body={'question':HarnessTests.unfamiliar,'request_key':'v2-disagreement'}
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            for _ in range(2):
                response=self.client.post('/api/dataops/research-questions',json=body)
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['proposal']['external_model_calls'],2)
                self.assertEqual(response.json()['proposal']['failure']['code'],'semantic_disagreement')
                self.assertNotIn('workspace',response.json())
            self.assertEqual(model.calls,2)
            self.assertTrue(response.json()['replayed'])
            self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'],[])

    def test_review_timeout_leaves_same_key_unknown_not_a_third_request(self):
        model=Sequence(draft(mode='filter_complete'),TimeoutError('unknown review'))
        body={'query':HarnessTests.unfamiliar,'request_key':'v2-review-timeout'}
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/chat',json=body)
            self.assertEqual(first.status_code,503,first.text)
            second=self.client.post('/chat',json=body)
            self.assertEqual(second.status_code,409,second.text)
            self.assertEqual(model.calls,2)

    def test_explicit_local_optout_and_prohibited_task_do_not_dispatch(self):
        self.client.get('/dataops/research')
        with patch('tcm.dataops.research_model.configured_model',side_effect=AssertionError('dispatch')):
            response=self.client.post('/api/dataops/research-questions',json={
                'question':HarnessTests.plain,'request_key':'v2-local-only','use_model':False})
            self.assertEqual(response.status_code,202,response.text)
            self.wait(response.json()['workspace']['id'])
            self.assertEqual(response.json()['proposal']['external_model_calls'],0)
            response=self.client.post('/chat',json={'query':'帮我治愈肺癌','request_key':'v2-boundary'})
            self.assertEqual(response.status_code,403,response.text)

    def test_two_call_task_delivers_and_queue_retry_reuses_paid_understanding(self):
        import hashlib
        owner=hashlib.sha256(self.client.cookies.get(self.app.SESSION_COOKIE_NAME).encode()).hexdigest()
        service=self.app.dataops_service;queue=service.queue()
        blocker,_=queue.enqueue(owner,'hold queue','v2-busy-blocker')
        body={'query':HarnessTests.unfamiliar,'request_key':'v2-busy-request'}
        model=Sequence(draft(mode='filter_complete'),draft(mode='filter_complete'))
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/chat',json=body)
            self.assertEqual(first.status_code,429,first.text)
            saved=service.store().by_request(owner,body['request_key'])
            self.assertEqual(saved['goal_understanding']['provider_requests'],2)
            second=self.client.post('/chat',json=body)
            self.assertEqual(second.status_code,429,second.text);self.assertEqual(model.calls,2)
            queue.cancel(owner,blocker['id'])
            retry=self.client.post('/chat',json=body)
            self.assertEqual(retry.status_code,200,retry.text)
            self.assertEqual(retry.json()['model_calls'],0)
            doc=self.wait(saved['id'])
            self.assertEqual(doc['status'],'complete')
            self.assertEqual(doc['result']['research']['reviewed_count'],2)
            self.assertEqual(doc['result']['rows'],[])
            self.assertEqual(model.calls,2)

    def test_parent_revision_preserves_original_artifact_and_scope(self):
        self.client.get('/dataops/research')
        first=self.client.post('/api/dataops/research-questions',json={
            'question':'整理Alpha和Beta的功效，生成表格','request_key':'v2-parent','use_model':False})
        parent=self.wait(first.json()['workspace']['id'])
        store=self.app.dataops_service.store()
        path=store.root/parent['id']/parent['artifact']['filename'];original=path.read_bytes()
        model=Sequence(draft(operation='append_fields',names=[],document_fields=['贮藏']))
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            response=self.client.post('/api/dataops/research-questions',json={
                'question':'增加贮藏，保留旧字段','request_key':'v2-child',
                'parent_id':parent['id'],'expected_revision':parent['revision']})
        self.assertEqual(response.status_code,202,response.text)
        child=self.wait(response.json()['workspace']['id'])
        self.assertEqual(child['goal']['query'],parent['goal']['query'])
        self.assertEqual(child['goal']['document_fields'],['功能与主治','贮藏'])
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(child['goal_understanding']['requirements']['operation'],'append_fields')
        self.assertEqual(model.calls,1)
