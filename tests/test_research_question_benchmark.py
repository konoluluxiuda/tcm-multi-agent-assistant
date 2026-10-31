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
from scripts import eval_research_questions as evaluation


class QuestionBenchmarkTests(unittest.TestCase):
    def test_scorer_rejects_wrong_columns_objects_and_selection_semantics(self):
        from tcm.dataops.research_contracts import ResearchGoal
        expected={'status':'ready','mode':'audit_named','names':['Alpha','Beta'],'fields':['功能与主治']}
        goal=ResearchGoal(query={'names':['Alpha','Beta'],'limit':20},attributes=['medicinal_part'],document_fields=['功能与主治'],
                          research={'mode':'audit_named','target_count':2}).model_dump()
        self.assertTrue(evaluation.score(expected,{'status':'ready','goal':goal})['passed'])
        for key,value in [('document_fields',['功能与主治','注意']),('research',{'mode':'select_complete','target_count':2})]:
            self.assertTrue(evaluation.score(expected,{'status':'ready','goal':{**goal,key:value}})['wrong_ready_goal'])
        self.assertTrue(evaluation.score({'status':'needs_input'},{'status':'ready','goal':goal})['unsafe_acceptance'])

    def test_mock_sdk_full_run_does_not_send_references_and_cannot_reexecute(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);c=fixture(root/'source')
            with sqlite3.connect(c['enrichment']) as db:
                db.execute('ALTER TABLE name_candidates ADD COLUMN evidence TEXT')
                for sid,eid,name in db.execute('SELECT herb_id,entry_id,name_zh FROM name_candidates').fetchall():
                    db.execute('UPDATE name_candidates SET evidence=? WHERE herb_id=?',(json.dumps({'entry_id':eid,'name_zh':name}),sid))
            release=root/'release';build(c,release)
            cases=json.loads(evaluation.SUITE.read_text())['cases']
            mapping={'白术':'Alpha','黄芪':'Beta','川芎':'Gamma','甘草':'Alpha','大黄':'Beta','当归':'Alpha'}
            for case in cases:
                for a,b in mapping.items():case['question']=case['question'].replace(a,b)
                case['question']=case['question'].replace('AlphaBeta','Alpha和Beta')
                for part in [case,*([case['parent']] if case.get('parent') else [])]:
                    if 'names' in part:part['names']=list(dict.fromkeys(mapping.get(n,n) for n in part['names']))
            suite=root/'suite.json';suite.write_text(json.dumps({'cases':cases,'reference_status':'fixture_only'}))
            requests=[]
            def response(req):
                value=json.loads(req.content);requests.append(value);payload=json.loads(value['messages'][-1]['content'])
                self.assertNotIn('reference',payload);self.assertNotIn('case',payload)
                self.assertFalse(value['parallel_tool_calls']);self.assertEqual(value['tools'][0]['function']['name'],'propose_research_goal')
                case=next(c for c in cases if c['question']==payload['question'])
                args=draft(names=case.get('names',[]),mode=case.get('mode','audit_named'),target_count=case.get('target_count'),
                           document_fields=case.get('fields',[]))
                if payload['parent']:
                    args.update(operation='append_fields',names=[],document_fields=['贮藏'],attributes=[],target_count=None)
                if case['status']=='needs_input':args.update(status='needs_input',issues=['合成澄清，非模型判断'])
                return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':1,'model':'fixture',
                    'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                        'tool_calls':[{'id':'goal','type':'function','function':{'name':'propose_research_goal','arguments':json.dumps(args)}}]}}],
                    'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
            def model():return ChatOpenAI(model='fixture',api_key='test-only',max_retries=0,max_tokens=1200,
                        base_url='https://api.deepseek.com',http_client=httpx.Client(transport=httpx.MockTransport(response)))
            out=root/'prepared'
            with patch.object(evaluation,'SUITE',suite):
                evaluation.prepare(release,out);report=evaluation.execute(release,out,model_factory=model)
                self.assertTrue(all(r['passed'] for r in report['records'] if r['strategy']=='model'),report)
                self.assertLessEqual(len(requests),8);self.assertEqual(report['actual_requests'],len(requests))
                before=len(requests)
                with self.assertRaises(FileExistsError):evaluation.execute(release,out,model_factory=model)
                self.assertEqual(len(requests),before)

    def test_strict_scope_reference_is_not_scored_as_default_scope(self):
        from tcm.dataops.research_contracts import ResearchGoal
        case={'status':'ready','mode':'select_complete','names':['Alpha','Beta'],
              'fields':['贮藏'],'target_count':1,'monograph_only':True}
        goal=ResearchGoal(query={'names':case['names'],'limit':20},attributes=['medicinal_part'],
                          document_fields=['贮藏'],research={'mode':'select_complete','target_count':1,'monograph_only':True}).model_dump()
        self.assertTrue(evaluation.score(case,{'status':'ready','goal':goal})['passed'])
        goal['research']['monograph_only']=False
        self.assertTrue(evaluation.score(case,{'status':'ready','goal':goal})['wrong_ready_goal'])
