"""Rules precede bounded model fallback, with no automatic real-source opt-in."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from features import can_auto_plan
from tcm.evidence_assistant import EvidenceAssistant
from tests.test_evidence_assistant import Model, answer

FLAGS = {'ENABLE_EVIDENCE_ASSISTANT': '1', 'ENABLE_EVIDENCE_AUTO_ROUTING': '1',
         'ENABLE_NATURAL_ROUTING': '1', 'EVIDENCE_ASSISTANT_DATA_MODE': 'synthetic',
         'RESEARCH_TOOLS_ONLY': '1', 'ENABLE_PHARMACOPOEIA_RESEARCH': '0', 'ENABLE_PUBMED': '0'}


class AutoRoutingTests(unittest.TestCase):
    def test_normal_input_reaches_real_graph_without_mode_switch(self):
        import app
        from agents import agent_decision as ad
        flow=EvidenceAssistant(Model([{'task':'evidence','field_queries':[{'entity':'示例草','field':'注意'}]},
                                     answer(quote='这些内容仅用于测试检索与引用链')]),synthetic=True)
        with patch.dict('os.environ',FLAGS), TestClient(app.app) as client, \
             patch('tcm.evidence_assistant.configured_assistant',return_value=flow), \
             patch.object(ad.LocalGuardrails,'check_input',side_effect=AssertionError('legacy guard reached')):
            self.assertIn('id="auto-intent-note"',client.get('/').text)
            response=client.post('/chat',json={'query':'示例草的使用风险？'})
            self.assertEqual(response.status_code,200,response.text)
            body=response.json()
            self.assertEqual(body['agent'],'EVIDENCE_ASSISTANT')
            self.assertEqual(body['routing']['reason'],'unmatched_rule_model_fallback')
            self.assertEqual(body['evidence_answer']['model_calls'],2)
            self.assertEqual(body['evidence_answer']['plan']['field_queries'][0]['field'],'注意')
            self.assertEqual(len(client.get('/conversation').json()['messages']),2)

    def test_rule_counts_and_explicit_commands_do_not_trigger_model(self):
        import app
        from agents import agent_decision as ad
        with patch.dict('os.environ',FLAGS), TestClient(app.app) as client, \
             patch('tcm.evidence_assistant.configured_assistant',side_effect=AssertionError('model reached')), \
             patch.object(ad,'execute_assistant_info',return_value={'status':'ok','message':'fixture count'}), \
             patch.object(ad.research_runtime,'execute',return_value=({'status':'research_disabled','message':'disabled'},None)):
            result=client.post('/chat',json={'query':'有多少药材'}).json()
            self.assertEqual(result['agent'],'ASSISTANT_INFO_AGENT')
            self.assertEqual(result['routing']['reason'],'dataset_question')
            result=client.post('/chat',json={'query':'/research 示例草的功效'}).json()
            self.assertEqual(result['routing']['mode'],'explicit')
            self.assertNotIn('evidence_answer',result)

    def test_disabled_or_real_source_mode_keeps_unmatched_input_before_memory(self):
        import app
        for change in ({'ENABLE_EVIDENCE_AUTO_ROUTING':'0'}, {'ENABLE_EVIDENCE_ASSISTANT':'0'},
                       {'ENABLE_NATURAL_ROUTING':'0'}, {'EVIDENCE_ASSISTANT_DATA_MODE':'local_sources'}):
            with self.subTest(change=change), patch.dict('os.environ',{**FLAGS,**change}), \
                 TestClient(app.app) as client, \
                 patch.object(app,'session_cookie',side_effect=AssertionError('memory reached')), \
                 patch.object(app,'process_query',side_effect=AssertionError('model reached')):
                response=client.post('/chat',json={'query':'示例草的使用风险？'})
                self.assertEqual(response.status_code,403)

    def test_unknown_command_and_over_budget_text_never_auto_plan(self):
        import app
        with patch.dict('os.environ',FLAGS), TestClient(app.app) as client, \
             patch.object(app,'process_query',side_effect=AssertionError('model reached')):
            for text in ('/unknown 示例草', '例'*1001, '   '):
                self.assertFalse(can_auto_plan(text))
                self.assertEqual(client.post('/chat',json={'query':text}).status_code,403)

    def test_failed_model_stops_without_legacy_retry(self):
        import app
        from agents import agent_decision as ad
        flow=EvidenceAssistant(Model([TimeoutError('private provider detail')]),synthetic=True)
        with patch.dict('os.environ',FLAGS), TestClient(app.app) as client, \
             patch('tcm.evidence_assistant.configured_assistant',return_value=flow), \
             patch.object(ad.LocalGuardrails,'check_input',side_effect=AssertionError('legacy fallback reached')):
            response=client.post('/chat',json={'query':'示例草的使用风险？'})
            self.assertEqual(response.status_code,502)
            self.assertEqual(response.json()['evidence_answer']['model_calls'],1)
            self.assertNotIn('private provider detail',response.text)

    def test_core_resolves_session_rules_before_fallback(self):
        import app
        from agents import agent_decision as ad
        route={'tool':'research','request':{'question':'它的注意'},
               'reason':'active_research_followup','mode':'natural'}
        with patch.dict('os.environ',FLAGS), TestClient(app.app) as client, \
             patch.object(ad,'resolve_tool_request',return_value=route) as resolver, \
             patch.object(ad.research_runtime,'execute',return_value=({'status':'candidates','message':'fixture followup'},None)), \
             patch('tcm.evidence_assistant.configured_assistant',side_effect=AssertionError('model reached')):
            result=client.post('/chat',json={'query':'自定义连续追问'}).json()
            self.assertEqual(result['routing']['reason'],'active_research_followup')
            self.assertIn('research_context',resolver.call_args.kwargs)
            self.assertNotIn('evidence_answer',result)


if __name__=='__main__':
    unittest.main()
