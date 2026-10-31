import json
import unittest
import httpx

from tcm.rag_model import BoundedChat,SingleRequestTransport
from tcm.rag_pipeline import LIMITS


class ModelTests(unittest.TestCase):
    def test_exact_wire_budget_usage_and_no_credential_in_payload(self):
        seen=[]
        def respond(request):
            data=json.loads(request.content);seen.append(data)
            self.assertNotIn('test-key',request.content.decode())
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"findings":[]}'}}],
                                             'usage':{'prompt_tokens':8,'completion_tokens':4,'total_tokens':12}})
        model=BoundedChat(endpoint='https://provider.invalid/chat/completions',model='test',key='test-key',
                          transport_factory=lambda:httpx.MockTransport(respond))
        r=model.generate({'question':'合成','sources':[]})
        self.assertEqual(r['stats']['requests'],1);self.assertEqual(r['stats']['usage']['total_tokens'],12)
        self.assertEqual(seen[0]['thinking'],{'type':'disabled'})
        with self.assertRaises(ValueError):model.generate({'question':'不允许重复'})
        self.assertEqual(len(seen),1)

    def test_timeout_and_http_error_never_retry(self):
        for fail in ('timeout','http'):
            calls=[]
            def respond(request):
                calls.append(1)
                if fail=='timeout':raise httpx.ReadTimeout('untrusted provider error')
                return httpx.Response(429,json={})
            model=BoundedChat(endpoint='https://provider.invalid/chat/completions',model='test',key='key',
                              transport_factory=lambda:httpx.MockTransport(respond))
            with self.assertRaises(httpx.HTTPError):model.generate({'question':'合成'})
            self.assertEqual(len(calls),1);self.assertEqual(model.stats['requests'],1)
            self.assertEqual(model.stats['outcome'],'unknown' if fail=='timeout' else 'http_429')

    def test_oversize_or_wrong_recipient_is_rejected_before_dispatch(self):
        calls=[]
        model=BoundedChat(endpoint='https://provider.invalid/chat/completions',model='test',key='key',
                          transport_factory=lambda:httpx.MockTransport(lambda r:calls.append(1)))
        with self.assertRaises(ValueError):model.generate({'question':'大'*(LIMITS['max_request_bytes'])})
        self.assertFalse(calls);self.assertEqual(model.stats['requests'],0)
        model=BoundedChat(endpoint='http://provider.invalid/chat/completions',model='test',key='key')
        with self.assertRaises(ValueError):model.generate({})

    def test_length_finish_is_not_accepted_as_complete_answer(self):
        model=BoundedChat(endpoint='https://provider.invalid/chat/completions',model='test',key='key',
            transport_factory=lambda:httpx.MockTransport(lambda r:httpx.Response(200,json={
                'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})))
        with self.assertRaisesRegex(ValueError,'Incomplete'):model.generate({})
        self.assertEqual(model.stats['requests'],1)

    def test_frozen_body_hash_change_is_blocked_before_dispatch(self):
        calls=[]
        model=BoundedChat(endpoint='https://provider.invalid/chat/completions',model='test',key='key',expected_sha='0'*64,
                         transport_factory=lambda:httpx.MockTransport(lambda r:calls.append(1)))
        with self.assertRaises(ValueError):model.generate({'question':'changed'})
        self.assertEqual(model.stats['requests'],0);self.assertFalse(calls)


if __name__=='__main__':unittest.main()
