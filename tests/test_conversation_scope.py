"""Continuous conversation and feedback context tests, all providers substituted."""
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from langchain_core.messages import HumanMessage, AIMessage
from tcm.dataops.conversation_scope import prepare
from tests import test_preset_requests as presets
from tests import test_requirements_feedback_http as feedback
from tests.test_requirements_v3_http import RequirementsSequence


class ScopeTests(unittest.TestCase):
    def test_short_reference_and_natural_intro(self):
        old=HumanMessage(content='我想了解牛膝和牡丹皮的资料。')
        context=prepare('这两味的性味和归经',SimpleNamespace(messages=[old]))
        self.assertTrue(context['inherited'])
        self.assertEqual(context['object_mentions'],['牛膝','牡丹皮'])
        self.assertTrue(prepare('这三味的性味',SimpleNamespace(messages=[old]))['needs_context'])
        self.assertTrue(prepare('这两味的性味',SimpleNamespace(messages=[]))['needs_context'])

    def test_latest_turn_only_and_count_must_match(self):
        old=HumanMessage(content='给出白术和黄芪的相关信息')
        self.assertEqual(prepare('这两个草药的性味是什么',SimpleNamespace(messages=[old]))['resolved_question'],
                         '查询白术和黄芪的性味')
        for messages in [[],[old,HumanMessage(content='换个话题')],[HumanMessage(content='查看白术的科名')]]:
            self.assertTrue(prepare('这两个草药的性味是什么',SimpleNamespace(messages=messages))['needs_context'])
        self.assertFalse(prepare('查看丹参的性味',SimpleNamespace(messages=[old]))['inherited'])


class ConversationHTTPTests(unittest.TestCase):
    setUp = presets.PresetHTTPTests.setUp
    wait = presets.PresetHTTPTests.wait
    def test_clarification_then_pronoun_then_repeat_keeps_both_objects(self):
        import app
        q='给出Alpha和Beta的相关信息'
        first=self.client.post('/chat',json={'query':q,'request_key':'scope-first'})
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(first.json()['assistant']['status'],'clarification_needed')
        again_first=self.client.post('/chat',json={'query':q,'request_key':'scope-first'})
        self.assertEqual(again_first.status_code,200,again_first.text)
        snapshot=app.agent_runtime.memory.load(self.client.cookies.get(app.SESSION_COOKIE_NAME))
        self.assertEqual([m.content for m in snapshot.messages if isinstance(m,HumanMessage)].count(q),1)
        body={'query':'这两个草药的性味是什么','request_key':'scope-follow'}
        second=self.client.post('/chat',json=body)
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(second.json()['status'],'success',second.text)
        self.assertEqual(len(second.json()['answer_steps']),4)
        self.assertEqual({s['route']['request']['name'] for s in second.json()['answer_steps']},{'Alpha','Beta'})
        again=self.client.post('/chat',json=body)
        self.assertEqual(again.status_code,200,again.text)
        self.assertTrue(again.json()['replayed'])
        third=self.client.post('/chat',json={'query':'这两个草药的科名是什么','request_key':'scope-next'})
        self.assertEqual(len(third.json()['answer_steps']),2,third.text)
        snapshot=app.agent_runtime.memory.load(self.client.cookies.get(app.SESSION_COOKIE_NAME))
        self.assertIn(q,[m.content for m in snapshot.messages if isinstance(m,HumanMessage)])

    def test_pronoun_without_context_never_binds(self):
        response=self.client.post('/chat',json={'query':'这两个草药的性味是什么','request_key':'scope-empty'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['assistant']['status'],'clarification_needed')
        self.assertNotIn('answer_steps',response.json())

    def test_rejected_request_persists_and_other_session_cannot_inherit(self):
        import app
        q='给出Alpha和Beta的未知资料'
        with patch.dict('os.environ',{'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'0'}), \
             patch('app.resolve_tool_request',return_value=None), \
             patch('app.can_auto_plan',return_value=False),patch('app.can_plan_intent',return_value=False):
            body={'query':q,'request_key':'scope-rejected'}
            rejected=self.client.post('/chat',json=body)
            self.assertEqual(rejected.status_code,403,rejected.text)
            rejected=self.client.post('/chat',json=body)
            self.assertEqual(rejected.status_code,403,rejected.text)
        from agents.conversation_store import ConversationStore
        store=ConversationStore(app.agent_runtime.memory.path)
        snapshot=store.load(self.client.cookies.get(app.SESSION_COOKIE_NAME))
        self.assertEqual([m.content for m in snapshot.messages if isinstance(m,HumanMessage)].count(q),1)
        reply=self.client.post('/chat',json={'query':'这两个草药的性味是什么','request_key':'scope-after-rejected'})
        self.assertEqual(reply.json()['status'],'success',reply.text)
        self.client.cookies.clear()
        self.client.get('/conversation')
        reply=self.client.post('/chat',json={'query':'这两个草药的性味是什么','request_key':'scope-after-rejected'})
        self.assertEqual(reply.json()['assistant']['status'],'clarification_needed',reply.text)

    def test_explicit_unqualified_properties(self):
        r=self.client.post('/chat',json={'query':'分别告诉我Alpha和Beta的性味','request_key':'scope-explicit'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(len(r.json()['answer_steps']),4,r.text)


class FeedbackContextHTTPTests(unittest.TestCase):
    setUp = feedback.FeedbackHTTPTests.setUp
    wait = feedback.FeedbackHTTPTests.wait
    def test_catalog_scope_survives_clarification_and_replay(self):
        import app
        good=feedback.draft()
        pending={**good,'status':'needs_input','requirements':[], 'unresolved':['需要哪些字段 family？']}
        model=RequirementsSequence(pending,good,good)
        inputs=[];invoke=model.invoke
        def capture(messages):
            inputs.append(json.loads(messages[-1]['content']))
            return invoke(messages)
        model.invoke=capture
        body={'query':'我想了解Alpha和Beta的资料。','request_key':'natural-scope'}
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/chat',json=body)
            self.assertEqual(first.status_code,200,first.text)
            self.assertNotIn('family',first.json()['assistant']['message'])
            again=self.client.post('/chat',json=body)
            self.assertEqual(again.status_code,200,again.text)
            snapshot=app.agent_runtime.memory.load(self.client.cookies.get(app.SESSION_COOKIE_NAME))
            human=[m for m in snapshot.messages if isinstance(m,HumanMessage)][-1]
            self.assertEqual(human.additional_kwargs['query_scope']['object_mentions'],['Alpha','Beta'])
            second=self.client.post('/chat',json={'query':'这两味的药用部位','request_key':'short-scope'})
            self.assertEqual(second.json()['status'],'success',second.text)
        self.assertEqual(model.calls,3)
        self.assertEqual(len(second.json()['answer_steps']),2)
        for payload in inputs[1:]:
            scope=payload['tool_observations']['conversation_scope']
            self.assertTrue(scope['inherited'])
            self.assertEqual(scope['object_mentions'],['Alpha','Beta'])

    def test_feedback_receives_failed_turn_context_in_initial_and_review(self):
        good=feedback.draft()
        pending={**good,'status':'needs_input','requirements':[], 'unresolved':['需要哪些属性？']}
        model=RequirementsSequence(pending,good,good)
        inputs=[]
        original=model.invoke
        def invoke(messages):
            inputs.append(json.loads(messages[-1]['content']))
            return original(messages)
        model.invoke=invoke
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/chat',json={'query':'分别告诉我Alpha和Beta的属性','request_key':'feedback-scope-first'})
            self.assertEqual(first.json()['assistant']['status'],'clarification_needed',first.text)
            second=self.client.post('/chat',json={'query':'这两个草药的药用部位是什么','request_key':'feedback-scope-next'})
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(second.json()['status'],'success',second.text)
        self.assertEqual(len(second.json()['answer_steps']),2)
        for payload in inputs[1:]:
            scope=payload['tool_observations']['conversation_scope']
            self.assertEqual(scope['object_mentions'],['Alpha','Beta'])
            self.assertTrue(scope['inherited'])
            self.assertEqual(payload['question'],'这两个草药的药用部位是什么')
        self.assertEqual(model.calls,3)
