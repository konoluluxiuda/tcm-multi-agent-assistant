from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from langchain_core.messages import AIMessage

from tests.dataops_fixture import make_sources
from tcm.dataops.goal_model import compile_draft, context, GoalResponseRejected, request_proposal


def draft(**changes):
    return {'status':'ready','operation':'new','names':['Alpha'],'filters':[], 'limit':50,
            'attributes':['medicinal_part','latin_name'],'document_fields':['功能与主治','注意'],
            'completion_policy':'audit','refresh_conflicts':False,'issues':[],'unresolved':[], **changes}


class GoalModelTests(unittest.TestCase):
    catalog = {'Alpha':['etcm2-local:'+'a'*64], 'Beta':['etcm2-local:'+'b'*64]}

    def parent(self):
        return {'id':'a'*32, 'revision':1, 'status':'complete','artifact':{'filename':'v1.xlsx'},
                'goal':{'query':{'names':['Alpha'],'limit':50},'completion_policy':'require_all_fields'},
                'result':{'rows':[{'source_id':self.catalog['Alpha'][0],
                                  'mapping_status':'medicinal_part_difference'}]}}

    def test_parent_scope_inheritance_and_strict_policy_are_host_owned(self):
        p = self.parent(); before = deepcopy(p)
        d = draft(operation='append_fields', names=[], attributes=[], document_fields=['贮藏'])
        result = compile_draft(d,'新增保存条件',self.catalog,parent=p)
        self.assertEqual(result['goal']['query']['source_ids'],self.catalog['Alpha'])
        self.assertEqual(result['goal']['document_fields'],['功能与主治','注意','贮藏'])
        self.assertEqual(result['goal']['completion_policy'],'require_all_fields')
        self.assertEqual(p,before)
        for bad in (dict(names=['Beta']),dict(limit=2),dict(operation='new'),
                    dict(filters=[{'field':'medicinal_part','op':'contains','value':'Root'}])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                compile_draft({**d,**bad},'改变对象',self.catalog,parent=p)

    def test_replace_only_document_fields_and_refresh_only_local_problem_ids(self):
        d = draft(operation='replace_document_fields', names=[], attributes=[],
                  document_fields=['贮藏'], refresh_conflicts=True)
        result = compile_draft(d,'只要保存条件并重查问题记录',self.catalog,parent=self.parent())
        self.assertEqual(result['goal']['document_fields'],['贮藏'])
        self.assertEqual(result['goal']['attributes'],['medicinal_part','latin_name'])
        self.assertEqual(result['refresh_source_ids'],self.catalog['Alpha'])
        with self.assertRaises(ValueError):
            compile_draft({**d,'document_fields':[]},'换掉全部列',self.catalog,parent=self.parent())

    def test_unknown_names_extra_write_parameters_and_unresolved_ready_rejected(self):
        for d in (draft(names=['Unknown']), draft(sql='DELETE FROM records'),
                  draft(document_fields=['疗效预测']),draft(unresolved=['还要翻译']),
                  draft(operation='append_fields'),draft(refresh_conflicts=True)):
            with self.subTest(d=d),self.assertRaises(ValueError):
                compile_draft(d,'合成请求',self.catalog)

    def test_deferral_cannot_return_an_executable_fragment(self):
        r = compile_draft(draft(status='needs_input',issues=['无法预测疗效，请去掉此要求']),
                          'Alpha功效并预测疗效',self.catalog)
        self.assertIsNone(r['goal'])
        with self.assertRaises(ValueError):
            compile_draft(draft(status='needs_input'),'未说明原因',self.catalog)

    def test_one_alias_cannot_silently_expand_past_record_limit(self):
        catalog={'Alpha':['etcm2-local:'+format(i,'064x') for i in range(51)]}
        with self.assertRaises(ValueError):
            compile_draft(draft(),'合成别名展开',catalog)

    def test_bounded_context_excludes_parent_values_evidence_and_unrelated_history(self):
        p = self.parent();p['result']['secret_payload']='must-not-send'
        value = context('加保存条件',self.catalog,p)
        self.assertNotIn('must-not-send',json.dumps(value))
        self.assertNotIn('source_id',json.dumps(value))
        with self.assertRaises(ValueError):context('x',{str(i):[] for i in range(129)})
        with self.assertRaises(ValueError):context('x'*2001,self.catalog)

    def test_explicit_opt_in_single_call_no_retry_on_timeout(self):
        outer = self
        class Chat:
            max_retries=0;max_tokens=1200;calls=0
            def bind_tools(self,tools,**kw):
                outer.assertFalse(kw['parallel_tool_calls']);return self
            def invoke(self,messages):
                self.calls+=1;raise TimeoutError('do not retry')
        chat=Chat()
        with self.assertRaises(PermissionError):request_proposal(chat,'Alpha',self.catalog)
        self.assertEqual(chat.calls,0)
        with self.assertRaises(TimeoutError):request_proposal(chat,'Alpha',self.catalog,authorized=True)
        self.assertEqual(chat.calls,1)

    def test_zero_multiple_unregistered_or_invalid_arguments_never_become_goals(self):
        class Chat:
            max_retries=0;max_tokens=1200;calls=0
            def bind_tools(self,*args,**kw):return self
            def invoke(self,messages):self.calls+=1;return self.reply
        chat=Chat()
        call={'id':'a','name':'propose_dataops_goal','args':draft()}
        for calls in ([],[call,{**call,'id':'b'}],[{**call,'name':'execute_sql'}],
                      [{**call,'args':draft(names=['Unknown'])}]):
            chat.reply=AIMessage(content='',tool_calls=calls)
            before=chat.calls
            with self.assertRaises(GoalResponseRejected) as caught:
                request_proposal(chat,'合成',self.catalog,authorized=True)
            self.assertEqual(chat.calls,before+1)
            self.assertNotIn('args',caught.exception.diagnostics)

    def test_real_sdk_wire_shape_and_single_proposal_response(self):
        from scripts.eval_dataops_live import configured_model
        seen=[]
        def respond(req):
            body=json.loads(req.content);seen.append(body)
            self.assertFalse(body['parallel_tool_calls'])
            self.assertEqual(body['thinking'],{'type':'disabled'})
            self.assertEqual(body['tools'][0]['function']['name'],'propose_dataops_goal')
            self.assertLess(len(req.content),65536)
            args=draft()
            return httpx.Response(200,json={'id':'goal-wire','object':'chat.completion','created':1,
                'model':'deepseek-flash','choices':[{'index':0,'finish_reason':'tool_calls',
                'message':{'role':'assistant','content':None,'tool_calls':[{'id':'g','type':'function',
                'function':{'name':'propose_dataops_goal','arguments':json.dumps(args)}}]}}],
                'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            r=request_proposal(configured_model('fixture-key',http),'整理Alpha功效注意',self.catalog,authorized=True)
        self.assertEqual(len(seen),1)
        self.assertEqual(r['status'],'ready')
        self.assertEqual(r['usage']['total_tokens'],30)


if __name__ == '__main__':unittest.main()
