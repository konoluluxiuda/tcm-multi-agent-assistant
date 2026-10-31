import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from langchain_openai import ChatOpenAI
from scripts import eval_understanding_compact as experiment
from tests.test_research_question import draft


class CompactProtocolTests(unittest.TestCase):
    def run_experiment(self,root,broken=False):
        requests=[]
        def factory(*,max_requests=1):
            count=[]
            def response(req):
                count.append(1);self.assertEqual(len(count),1)
                body=json.loads(req.content);requests.append(body)
                self.assertLessEqual(len(req.content),65536)
                self.assertFalse(body['parallel_tool_calls'])
                if broken:raise httpx.ReadTimeout('unknown outcome')
                context=json.loads(body['messages'][-1]['content']);question=context['question']
                self.assertNotIn('reference',context)
                if '平台' in question:tool,args='submit_query_intent',{'action':'capabilities'}
                elif '数据' in question:tool,args='submit_query_intent',{'action':'dataset_info'}
                elif context['parent']:
                    tool,args='propose_research_goal',draft(operation='append_fields',names=[],document_fields=['贮藏'])
                else:tool,args='propose_research_goal',draft(document_fields=['功能与主治','贮藏'])
                self.assertIn(tool,[t['function']['name'] for t in body['tools']])
                return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':1,'model':'fixture',
                    'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                        'tool_calls':[{'id':'call','type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]}}],
                    'usage':{'prompt_tokens':100,'completion_tokens':10,'total_tokens':110}})
            return ChatOpenAI(model='fixture',api_key='test-only',base_url='https://api.deepseek.com',max_tokens=1200,max_retries=0,
                              http_client=httpx.Client(transport=httpx.MockTransport(response)))
        experiment.prepare(root)
        with patch.dict('os.environ',{'RAG_MODEL':'fixture','RAG_API_KEY':'test-only','RAG_BASE_URL':'https://api.deepseek.com'}), \
             patch('tcm.dataops.research_model.configured_model',side_effect=factory):
            report=experiment.execute(root)
            with self.assertRaises(FileExistsError):experiment.execute(root)
        return report,requests

    def test_both_profiles_fixed_delivery_parent_preservation_and_single_use_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            report,requests=self.run_experiment(Path(tmp)/'run')
            self.assertEqual(report['status'],'passed',report)
            self.assertEqual(report['actual_requests'],8);self.assertEqual(len(requests),8)
            self.assertFalse(report['independent_user_accuracy'])
            for case in ('S01','S02','S03','S04'):
                rows={r['profile']:r for r in report['records'] if r['id']==case}
                self.assertLess(rows['compact_v2']['input_utf8_bytes'],rows['full_v1']['input_utf8_bytes'])
            self.assertTrue(all(r['parent_preserved'] for r in report['records'] if r['id']=='S04'))

    def test_provider_error_stops_later_combinations_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            report,requests=self.run_experiment(Path(tmp)/'run',broken=True)
            self.assertEqual(len(requests),1);self.assertTrue(report['provider_stopped'])
            self.assertEqual(sum(r['status']=='not_run_after_provider_error' for r in report['records']),7)


if __name__=='__main__':unittest.main()
