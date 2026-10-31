import json
from pathlib import Path
import tempfile
import unittest

import httpx
from langchain_core.messages import AIMessage

from scripts.eval_dataops_goals import budget_transport, evaluate, preview, score, SUITE
from tests.test_dataops_goal_model import draft


class GoalEvaluationTests(unittest.TestCase):
    def test_reference_checks_semantics_not_just_schema(self):
        case={'expected_status':'ready','expected_goal':{'query':{'names':['Alpha'],'limit':50}}}
        catalog={'Alpha':['etcm2-local:'+'a'*64]}
        for field,value in (('document_fields',['贮藏']),('completion_policy','require_all_fields'),
                            ('attributes',['family'])):
            proposal={'status':'ready','goal':{**case['expected_goal'],field:value}}
            self.assertFalse(score(case,proposal,catalog)['passed'])
        result=score({'expected_status':'needs_input'},{'status':'ready'},catalog)
        self.assertTrue(result['unsafe_acceptance'])
        self.assertFalse(result['passed'])
        self.assertFalse(score({'expected_status':'needs_input'},{'status':'proposal_error'},catalog)['passed'])

    def test_protocol_is_bounded_and_preview_has_no_model(self):
        p=preview()
        self.assertEqual(len(p['case_ids']),12)
        self.assertEqual(p['max_requests_total'],12)
        self.assertEqual(p['max_requests_per_case'],1)
        self.assertEqual(p['real_model_calls'],0)
        self.assertEqual(p['retries'],0)
        self.assertFalse(p['http_integration'])

    def request(self,http):
        return http.post('https://api.deepseek.com/chat/completions',json={
            'model':'deepseek-flash','max_tokens':1200,'thinking':{'type':'disabled'},
            'parallel_tool_calls':False,'tool_choice':'required',
            'tools':[{'type':'function','function':{'name':'propose_dataops_goal'}}], 'messages':[]})

    def test_dispatch_reserved_before_send_and_unknown_stops_remaining_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);t=budget_transport(out);t.case_id='C01'
            def fail(req):
                self.assertEqual(json.loads((out/'dispatch.json').read_text())[0]['outcome'],'unknown')
                raise httpx.ReadTimeout('unknown outcome')
            t.inner.close();t.inner=httpx.MockTransport(fail)
            with httpx.Client(transport=t) as http:
                with self.assertRaises(httpx.ReadTimeout):self.request(http)
                t.case_id='C02'
                with self.assertRaises(RuntimeError):self.request(http)
            self.assertEqual(len(t.requests),1)

    def test_duplicate_case_cannot_be_resent_even_when_other_cases_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            t=budget_transport(Path(tmp));t.case_id='C01'
            t.inner.close();t.inner=httpx.MockTransport(lambda r:httpx.Response(200,json={
                'choices':[{'message':{'tool_calls':[]}}]}))
            with httpx.Client(transport=t) as http:
                self.request(http)
                with self.assertRaises(RuntimeError):self.request(http)
            self.assertEqual(len(t.requests),1)

    def test_all_questions_end_to_end_with_labelled_fake_and_independent_source_oracle(self):
        suite=json.loads(SUITE.read_text())
        class Chat:
            max_retries=0;max_tokens=1200;calls=0
            def bind_tools(self,*args,**kw):return self
            def invoke(self,messages):
                self.calls+=1
                question=json.loads(messages[-1]['content'])['question']
                case=next(c for c in suite['cases'] if c['question']==question)
                goal=case['expected_goal']
                if goal is None:
                    args=draft(status='needs_input',issues=['合成澄清，非真实模型判断'])
                else:
                    args=draft(names=goal['query'].get('names',[]),filters=goal['query'].get('filters',[]),
                               limit=goal['query']['limit'],document_fields=goal.get('document_fields',['功能与主治','注意']))
                    if case['parent_goal']:
                        args.update(names=[],attributes=[],operation='replace_document_fields'
                                    if case['case_id']=='G04' else 'append_fields')
                        if args['operation']=='append_fields':args['document_fields']=['贮藏']
                return AIMessage(content='',tool_calls=[{'id':'goal','name':'propose_dataops_goal','args':args}])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'fake';chat=Chat();r=evaluate(out,chat)
            generated=[row for row in r['results'] if row['strategy']=='G']
            self.assertEqual(len(generated),12)
            self.assertTrue(all(row['status']=='passed' for row in generated),generated)
            self.assertEqual(chat.calls,12)
            self.assertEqual(r['actual_provider_requests'],0)
            self.assertTrue(all(row['fixed_delivery']['external_model_calls']==0 for row in generated if row['fixed_delivery']))
            with self.assertRaises(FileExistsError):evaluate(out,chat)


if __name__ == '__main__':unittest.main()
