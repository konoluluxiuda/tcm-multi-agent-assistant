import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from langchain_openai import ChatOpenAI
from scripts import check_unified_harness_live as smoke
from tests.test_research_question import draft


class SmokeProtocolTests(unittest.TestCase):
    def run_smoke(self,root,broken=False):
        requests=[]
        def model(*,max_requests=1):
            local=[]
            def response(req):
                body=json.loads(req.content);requests.append(body);local.append(1)
                self.assertLessEqual(len(local),max_requests);self.assertLessEqual(len(req.content),65536)
                self.assertFalse(body['parallel_tool_calls'])
                if broken:raise httpx.ReadTimeout('synthetic unknown outcome')
                context=json.loads(body['messages'][-1]['content'])
                self.assertNotIn('reference',context)
                if 'question' in context:
                    if '交付哪些材料' in context['question']:tool,args='submit_query_intent',{'action':'capabilities'}
                    else:tool,args='propose_research_goal',draft(document_fields=['功能与主治','贮藏'])
                elif not context['listed']:tool,args='list_candidates',{}
                elif context['unread']:tool,args='inspect_candidates',{'source_ids':context['unread'][:5]}
                else:tool,args='deliver_result',{'source_ids':[],'allow_partial':True}
                if 'question' not in context:args={'purpose':'按合成观察调用工具',**args}
                return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':1,'model':'fixture',
                    'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                        'tool_calls':[{'id':'action','type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
            return ChatOpenAI(model='fixture',api_key='test-only',base_url='https://api.deepseek.com',max_tokens=1200,max_retries=0,
                              http_client=httpx.Client(transport=httpx.MockTransport(response)))
        smoke.prepare(root)
        with patch.dict('os.environ',{'RAG_MODEL':'fixture','RAG_API_KEY':'test-only','RAG_BASE_URL':'https://api.deepseek.com'}), \
             patch('tcm.dataops.research_model.configured_model',side_effect=model):
            report=smoke.execute(root)
            count=len(requests)
            with self.assertRaises(FileExistsError):smoke.execute(root)
            self.assertEqual(len(requests),count)
        return report,requests

    def test_real_sdk_mock_transport_covers_api_handoff_replay_and_native_empty_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            report,requests=self.run_smoke(Path(tmp)/'run')
            self.assertEqual(report['status'],'passed',report)
            self.assertEqual(report['actual_requests'],5);self.assertEqual(len(requests),5)
            self.assertEqual(report['results'][-1]['rows'],0)
            self.assertFalse(report['real_corpus_sent'])

    def test_unknown_dispatch_stops_remaining_stages_and_frozen_run_cannot_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            report,requests=self.run_smoke(Path(tmp)/'run',broken=True)
            self.assertEqual(len(requests),1);self.assertTrue(report['provider_stopped'])
            self.assertEqual([r['status'] for r in report['results']],['failed','not_run_after_provider_error','not_run_after_provider_error'])


if __name__=='__main__':unittest.main()
