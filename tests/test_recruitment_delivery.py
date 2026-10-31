from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from scripts.rehearse_recruitment_demo import rehearse_dataops
from tests.dataops_fixture import make_sources
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_model import ResearchFixed
from tcm.dataops.workspace import Workspaces


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        _,sources=make_sources(self.root/'source');self.store=Workspaces(self.root/'work');self.posts=[]
        def run(goal,key,parent=None):
            with sources() as s:
                doc,_=self.store.reserve_agent('fixture-owner',key,goal.model_dump(),s.revision,question='合成排练',
                    parent_id=parent['id'] if parent else None,expected_revision=parent['revision'] if parent else None)
            d=ResearchEngine(self.store,'fixture-owner',doc['id'],sources,ResearchFixed()).run()
            d['goal_understanding']={'provider_requests':0};return d
        goal=ResearchGoal(query={'names':['Alpha','Beta'],'limit':20},attributes=['medicinal_part'],
                          document_fields=['功能与主治','注意'],research={'mode':'audit_named','target_count':2})
        first=run(goal,'demo-first');second=run(goal.model_copy(update={'document_fields':goal.document_fields+['贮藏']}),'demo-second',first)
        selected=run(goal.model_copy(update={'document_fields':['功能与主治'],'research':goal.research.model_copy(update={'mode':'select_complete','target_count':1})}),'demo-selection')
        self.docs=[first,second,selected]
        self.examples={'names':['Alpha','Beta'],'question':'整理Alpha和Beta功效注意',
            'selection_names':['Alpha','Beta'],'selection_fields':['功能与主治'],'selection_count':1,
            'selection_question':'从Alpha和Beta挑一条功效齐全的','ambiguous_question':'Alpha来源',
            'unsupported_question':'Alpha联网'}

    def client(self,*,stale=False,native=False,tamper=False,model_calls=0):
        docs=deepcopy(self.docs);docs[0]['goal_understanding']['provider_requests']=model_calls
        active=[]
        def transport(req):
            path=req.url.path
            if req.method=='POST':
                body=json.loads(req.content);self.posts.append(body);self.assertIs(body['use_model'],False)
                q=body['question']
                index={self.examples['question']:0,'增加贮藏，保留旧字段':1,self.examples['selection_question']:2}.get(q)
                if index is None:return httpx.Response(200,json={'proposal':{'status':'needs_input','goal':None,'external_model_calls':0}})
                d=docs[index];active.append(d)
                if index==1:self.assertEqual(body['parent_id'],docs[0]['id']);self.assertEqual(body['expected_revision'],1)
                return httpx.Response(202,json={'workspace':d})
            if path=='/dataops/research':return httpx.Response(200,text='fixture')
            if path=='/api/runtime':return httpx.Response(200,json={'restart_required':stale,'source_revision':'fixture','disk_revision':'changed' if stale else 'fixture'})
            if path=='/api/dataops/capabilities':return httpx.Response(200,json={'research_selection':{'enabled':True,'driver':'native' if native else 'fixed','execution_model_enabled':native}})
            if path=='/api/dataops/workspaces':return httpx.Response(200,json={'items':active})
            identifier=path.split('/')[-2] if path.endswith('/artifact') else path.split('/')[-1]
            d=next(d for d in docs if d['id']==identifier)
            if path.endswith('/artifact'):
                data=(self.store.root/d['id']/d['artifact']['filename']).read_bytes()
                return httpx.Response(200,content=data+(b'tampered' if tamper else b''))
            return httpx.Response(200,json={'workspace':d,'job':{'status':'complete'}})
        c=httpx.Client(base_url='http://127.0.0.1:19999',transport=httpx.MockTransport(transport));self.addCleanup(c.close);return c

    def test_downloaded_files_parent_cells_and_controls_verified_without_model(self):
        out=self.root/'pass';r=rehearse_dataops('http://127.0.0.1:19999',out,client=self.client(),examples=self.examples)
        self.assertEqual(r['status'],'passed',r);self.assertEqual(len(self.posts),5)
        self.assertEqual(len(list(out.glob('*.xlsx'))),3);self.assertTrue(r['old_cells_unchanged'])
        self.assertTrue(r['model_calls_verified']);self.assertEqual(r['external_model_calls'],0)
        self.assertNotIn('测试原文',json.dumps(r,ensure_ascii=False))
        with self.assertRaises(FileExistsError):rehearse_dataops('http://127.0.0.1:19999',out,client=self.client(),examples=self.examples)
        self.assertEqual(len(self.posts),5)

    def test_stale_or_native_server_stops_before_creating_any_task(self):
        for index,kw in enumerate(({'stale':True},{'native':True})):
            r=rehearse_dataops('http://127.0.0.1:19999',self.root/('preflight-'+str(index)),client=self.client(**kw),examples=self.examples)
            self.assertEqual(r['status'],'failed');self.assertEqual(r['phase'],'runtime')
        self.assertFalse(self.posts)

    def test_corrupt_artifact_is_failure_instead_of_a_successful_demo(self):
        r=rehearse_dataops('http://127.0.0.1:19999',self.root/'bad-file',client=self.client(tamper=True),examples=self.examples)
        self.assertEqual(r['status'],'failed');self.assertEqual(r['reason'],'artifact_hash_mismatch')
        self.assertEqual(len(self.posts),1)

    def test_unexpected_model_usage_is_reported_instead_of_claiming_zero(self):
        r=rehearse_dataops('http://127.0.0.1:19999',self.root/'bad-call',client=self.client(model_calls=1),examples=self.examples)
        self.assertEqual(r['status'],'failed');self.assertEqual(r['reason'],'unexpected_model_request')
        self.assertEqual(r['external_model_calls'],1);self.assertFalse(r['model_calls_verified'])
