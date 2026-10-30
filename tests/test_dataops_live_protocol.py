import json
from pathlib import Path
import tempfile
import unittest
import httpx

from scripts.eval_dataops_live import preview,budget_transport,configured_model


class LiveProtocolTests(unittest.TestCase):
    def test_preview_is_synthetic_and_does_not_construct_provider(self):
        spec=preview()
        self.assertEqual([c['case_id'] for c in spec['cases']],['B01','B04','R01'])
        self.assertEqual(spec['real_model_calls'],0)
        self.assertEqual(spec['max_requests_total'],36)
        self.assertIn('real ETCM',spec['excluded'])
        self.assertEqual(spec['thinking'],{'type':'disabled'})

    def test_transport_enforces_recipient_bytes_and_global_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen=[]
            def handler(request):seen.append(1);return httpx.Response(200,json={'ok':True})
            transport=budget_transport(Path(tmp),inner=httpx.MockTransport(handler))
            with httpx.Client(transport=transport) as client:
                body={'model':'deepseek-flash','max_tokens':1200,'messages':[], 'thinking':{'type':'disabled'}}
                for url,data in [('https://evil.invalid/chat/completions',body),
                    ('https://api.deepseek.com/other',body),
                    ('https://api.deepseek.com/chat/completions',{**body,'max_tokens':1201}),
                    ('https://api.deepseek.com/chat/completions',{**body,'thinking':{'type':'enabled'}}),
                    ('https://api.deepseek.com/chat/completions',{**body,'x':'x'*65537})]:
                    with self.assertRaises(RuntimeError):client.post(url,json=data)
                self.assertEqual(seen,[])
                for _ in range(36):client.post('https://api.deepseek.com/chat/completions',json=body)
                with self.assertRaises(RuntimeError):client.post('https://api.deepseek.com/chat/completions',json=body)
            self.assertEqual(len(seen),36)
            saved=json.loads((Path(tmp)/'dispatch.json').read_text())
            self.assertEqual(len(saved),36)
            self.assertNotIn('messages',json.dumps(saved))

    def test_unknown_transport_dispatch_is_recorded_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            attempts=[]
            def handler(request):attempts.append(1);raise httpx.ReadTimeout('synthetic timeout')
            transport=budget_transport(Path(tmp),inner=httpx.MockTransport(handler))
            with httpx.Client(transport=transport) as client:
                with self.assertRaises(httpx.ReadTimeout):client.post('https://api.deepseek.com/chat/completions',
                    json={'model':'deepseek-flash','max_tokens':1200,'thinking':{'type':'disabled'}})
            self.assertEqual(len(attempts),1)
            self.assertEqual(json.loads((Path(tmp)/'dispatch.json').read_text())[0]['outcome'],'unknown')

    def test_provider_error_receipt_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            def handler(request):return httpx.Response(400,json={'error':{'type':'invalid_request_error',
                'message':'Bad request test-only-key','param':'tool_choice'}})
            transport=budget_transport(Path(tmp),inner=httpx.MockTransport(handler))
            with httpx.Client(transport=transport) as client:
                response=client.post('https://api.deepseek.com/chat/completions',
                    headers={'Authorization':'Bearer test-only-key'},
                    json={'model':'deepseek-flash','max_tokens':1200,'thinking':{'type':'disabled'}})
                self.assertEqual(response.status_code,400)
            saved=(Path(tmp)/'dispatch.json').read_text()
            self.assertNotIn('test-only-key',saved)
            self.assertIn('[REDACTED]',saved)

    def test_actual_sdk_wire_disables_thinking_with_required_tool_choice(self):
        from tcm.dataops.model import OpenAIToolDriver
        with tempfile.TemporaryDirectory() as tmp:
            seen=[]
            def handler(request):
                data=json.loads(request.read());seen.append(data)
                return httpx.Response(200,json={'id':'offline-completion','object':'chat.completion','created':0,
                    'model':'deepseek-flash','choices':[{'index':0,'finish_reason':'tool_calls','message':{
                    'role':'assistant','content':'','tool_calls':[{'id':'call-1','type':'function','function':{
                        'name':'query_records','arguments':json.dumps({'purpose':'合成读取'})}}]}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
            transport=budget_transport(Path(tmp),inner=httpx.MockTransport(handler))
            with httpx.Client(transport=transport) as http:
                model=configured_model('offline-key',http)
                action=OpenAIToolDriver(model,authorized=True).decide({'goal':{},'observation':None})
            self.assertEqual(action['tool'],'query_records')
            self.assertEqual(len(seen),1)
            self.assertEqual(seen[0]['thinking'],{'type':'disabled'})
            self.assertEqual(seen[0]['tool_choice'],'required')
            self.assertFalse(seen[0]['parallel_tool_calls'])
            self.assertEqual(seen[0]['max_tokens'],1200)
            self.assertNotIn('max_completion_tokens',seen[0])
            receipt=json.loads((Path(tmp)/'dispatch.json').read_text())[0]['reply_summary']
            self.assertEqual(receipt,{'tool_call_count':1,'tool_names':['query_records'],
                'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})

    def test_success_response_diagnostics_do_not_save_text_or_arguments(self):
        from tcm.dataops.model import OpenAIToolDriver,ModelResponseRejected
        marker='synthetic-private-marker'
        with tempfile.TemporaryDirectory() as tmp:
            def handler(request):
                return httpx.Response(200,json={'id':'offline-completion','object':'chat.completion','created':0,
                    'model':'deepseek-flash','choices':[{'index':0,'finish_reason':'tool_calls','message':{
                    'role':'assistant','content':marker,'tool_calls':[
                        {'id':'one','type':'function','function':{'name':'query_records',
                            'arguments':json.dumps({'purpose':marker})}},
                        {'id':'two','type':'function','function':{'name':marker,'arguments':'{}'}}]}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
            transport=budget_transport(Path(tmp),inner=httpx.MockTransport(handler))
            with httpx.Client(transport=transport) as http:
                with self.assertRaises(ModelResponseRejected):
                    OpenAIToolDriver(configured_model('offline-key',http),authorized=True).decide({})
            saved=(Path(tmp)/'dispatch.json').read_text()
            self.assertNotIn(marker,saved)
            receipt=json.loads(saved)[0]['reply_summary']
            self.assertEqual(receipt['tool_call_count'],2)
            self.assertEqual(receipt['tool_names'],['query_records','unregistered'])
            self.assertEqual(receipt['usage']['total_tokens'],15)


if __name__=='__main__':unittest.main()
