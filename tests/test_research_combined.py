import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from langchain_openai import ChatOpenAI
from tests.rag_release_fixture import fixture
from tests.test_research_question import draft
from tcm.data_release import build
from scripts import eval_research_combined as ev


class CombinedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        config=fixture(self.root/'source')
        with sqlite3.connect(config['enrichment']) as db:
            db.execute('ALTER TABLE name_candidates ADD COLUMN evidence TEXT')
            for sid,eid,name in db.execute('SELECT herb_id,entry_id,name_zh FROM name_candidates').fetchall():
                db.execute('UPDATE name_candidates SET evidence=? WHERE herb_id=?',(json.dumps({'entry_id':eid,'name_zh':name}),sid))
        self.release=self.root/'release';build(config,self.release)
        self.cases=json.loads(ev.SUITE.read_text())['cases']
        mapping={'桔梗':'Alpha','牡丹皮':'Beta','陈皮':'Gamma','薄荷':'Alpha'}
        for c in self.cases:
            for a,b in mapping.items():c['question']=c['question'].replace(a,b)
            if 'names' in c:c['names']=list(dict.fromkeys(mapping[n] for n in c['names']))
        suite=self.root/'suite.json';suite.write_text(json.dumps({'version':'synthetic_combined_fixture','cases':self.cases,'reference_status':'synthetic_fixture_only'}))
        p=patch.object(ev,'SUITE',suite);p.start();self.addCleanup(p.stop)
        self.calls=[]

    def model(self,limit):
        def response(req):
            body=json.loads(req.content);self.calls.append(body)
            self.assertFalse(body['parallel_tool_calls'])
            context=json.loads(body['messages'][-1]['content'])
            self.assertNotIn('witness',context);self.assertNotIn('reference_status',context)
            if body['tools'][0]['function']['name']=='propose_research_goal':
                c=next(c for c in self.cases if c['question']==context['question'])
                args=draft(names=c['names'],mode=c['mode'],target_count=c['target_count'] if c['mode']=='select_complete' else None,
                           document_fields=c['fields'])
                if context['parent']:args.update(operation='append_fields',names=[],target_count=None,document_fields=['注意','贮藏'])
                tool='propose_research_goal'
            else:
                if not context['listed']:tool,args='list_candidates',{}
                elif context['unread']:tool,args='inspect_candidates',{'source_ids':context['unread'][:5]}
                else:
                    tool='deliver_result'
                    ids=context['parent_records'] or ([r['source_id'] for r in context['candidates']] if context['task_mode']=='audit_named' else context['eligible'][:context['target_count']])
                    args={'source_ids':ids,'allow_partial':len(context['eligible'])<context['target_count']}
                args={'purpose':'模拟观察动作，非真实模型决策',**args}
            return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':1,'model':'fixture',
                'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                    'tool_calls':[{'id':'action','type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]}}],
                'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
        return ChatOpenAI(model='fixture',api_key='test-only',base_url='https://api.deepseek.com',max_tokens=1200,max_retries=0,
                          http_client=httpx.Client(transport=httpx.MockTransport(response)))

    def test_same_goal_fixed_and_native_full_delivery_no_reference_leak_or_reexecution(self):
        out=self.root/'prepared';ev.prepare(self.release,out);r=ev.execute(self.release,out,model_factory=self.model)
        self.assertTrue(all(x['passed'] for x in r['records'] if x['strategy'] in ('G','A')),r)
        self.assertEqual(r['actual_requests'],len(self.calls));self.assertLessEqual(r['actual_requests'],24)
        self.assertEqual(sum('goal' in json.loads((out/f'request_{i:02d}.private.json').read_text())['stage'] for i in range(1,len(self.calls)+1)),2)
        self.assertFalse(any(x['execution_started'] for x in r['records'] if x['id'] in ('C03','C04')))
        before=len(self.calls)
        with self.assertRaises(FileExistsError):ev.execute(self.release,out,model_factory=self.model)
        self.assertEqual(before,len(self.calls))

    def test_unknown_goal_outcome_stops_all_later_model_stages_without_retry(self):
        def broken(limit):
            def response(req):self.calls.append(req);raise httpx.ReadTimeout('simulated unknown outcome')
            return ChatOpenAI(model='fixture',api_key='test-only',base_url='https://api.deepseek.com',max_tokens=1200,max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(response)))
        out=self.root/'failed';ev.prepare(self.release,out);r=ev.execute(self.release,out,model_factory=broken)
        self.assertEqual(len(self.calls),1);self.assertTrue(r['provider_stopped'])
        self.assertEqual(r['actual_requests'],1)
        self.assertFalse(any(x['execution_started'] for x in r['records'] if x['strategy'] in ('G','A')))
        self.assertEqual(len(list(out.glob('request_*.private.json'))),1)
        self.assertFalse(list(out.glob('response_*.private.json')))

    def test_frozen_source_change_rejected_before_any_provider_request(self):
        out=self.root/'changed';ev.prepare(self.release,out)
        p=out/'C01_prepared.private.json';p.write_text(p.read_text()+' ')
        with self.assertRaises(ValueError):ev.execute(self.release,out,model_factory=self.model)
        self.assertFalse(self.calls)

    def test_alternate_suite_only_runs_scheduled_native_cases_and_freezes_input(self):
        suite=self.root/'alternate.json'
        suite.write_text(json.dumps({'version':'fixture_subset','cases':self.cases,'reference_status':'synthetic_fixture_only',
                                     'native_case_ids':['C01'],'max_total_requests':8}))
        out=self.root/'subset';protocol=ev.prepare(self.release,out,suite)
        self.assertEqual(protocol['budget']['total_requests'],8)
        r=ev.execute(self.release,out,model_factory=self.model)
        self.assertEqual(r['by_strategy']['A']['total'],1)
        self.assertTrue(all(x['passed'] is None and not x['execution_started'] for x in r['records']
                            if x['strategy']=='A' and x['id']!='C01'))
        self.assertFalse(any(json.loads(p.read_text())['stage']=='C02_A' for p in out.glob('request_*.private.json')))
        changed=self.root/'tampered';ev.prepare(self.release,changed,suite)
        p=changed/'suite.private.json';p.write_text(p.read_text()+' ')
        before=len(self.calls)
        with self.assertRaisesRegex(ValueError,'suite changed'):ev.execute(self.release,changed,model_factory=self.model)
        self.assertEqual(len(self.calls),before)

    def test_invalid_suite_ids_or_native_ids_rejected_without_creating_output(self):
        for i,options in enumerate(({'native_case_ids':['missing']},{'cases':[{**self.cases[0],'id':'../escape'}]})):
            suite=self.root/f'invalid-{i}.json'
            suite.write_text(json.dumps({'version':'invalid','cases':self.cases,'reference_status':'synthetic',**options}))
            out=self.root/f'rejected-{i}'
            with self.assertRaises(ValueError):ev.prepare(self.release,out,suite)
            self.assertFalse(out.exists())
