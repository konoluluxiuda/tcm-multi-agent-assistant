from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import httpx
from scripts import eval_user_chat_replay as replay

RUNTIME={'started_at':'fixed-start','source_revision':'fixture','disk_revision':'fixture','restart_required':False,
 'intent_model_enabled':False,'dataops':{'research_selection':{'enabled':True,'driver':'fixed',
 'execution_model_enabled':False,'question_model_enabled':False}}}


def suite(turns=None):
 return {'version':'fixture','authorship':{'kind':'observed_user_exposed','independent_acceptance':False},
  'cases':[{'id':'Q01','role':'business','turns':turns or [{'query':'original wording','checks':[{'path':'/assistant/topic','equals':'capabilities'}]}]}]}


class ReplayTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.posts=[]
 def factory(self,*,status=200,model=False,replay_calls=0,changed=False,docs=None,store=None,tamper=False):
  seen={};active=[]
  def request(req):
   path=req.url.path
   if path=='/api/runtime':
    d=deepcopy(RUNTIME);d['dataops']['research_selection']['question_model_enabled']=model
    if changed:d['started_at']='restarted'
    return httpx.Response(200,json=d)
   if path=='/dataops/research':return httpx.Response(200,text='fixture')
   if path.startswith('/ready/'):return httpx.Response(200,json={'status':'ready','revision':'fixture'})
   if path=='/api/dataops/workspaces':return httpx.Response(200,json={'items':active})
   if req.method=='POST':
    body=json.loads(req.content);self.posts.append(body);key=body['request_key'];repeat=key in seen
    if not repeat:seen[key]=len(seen)
    if status>=400:return httpx.Response(status,json={'detail':'fixture unavailable'})
    value={'model_calls':replay_calls if repeat else int(model),'assistant':{'topic':'capabilities'}}
    if docs:
     d=docs[seen[key]];active.append(d);value['assistant'].update(workspace_id=d['id'])
    return httpx.Response(status,json=value)
   identifier=path.split('/')[-2] if path.endswith('/artifact') else path.split('/')[-1]
   d=next(d for d in docs if d['id']==identifier)
   if path.endswith('/artifact'):
    data=(store.root/d['id']/d['artifact']['filename']).read_bytes()
    return httpx.Response(200,content=data+(b'corrupt' if tamper else b''))
   return httpx.Response(200,json={'workspace':d,'job':{'status':'complete'}})
  return lambda **kw:httpx.Client(**kw,transport=httpx.MockTransport(request))
 def prepare(self,data=None,**kw):
  file=self.root/'suite.json';file.write_text(json.dumps(data or suite()));out=self.root/'run'
  replay.prepare(file,out,{'local':'http://127.0.0.1:9999'},self.root,**kw);return out

 def test_one_execution_original_question_and_immutable_receipts(self):
  factory=self.factory();out=self.prepare(client_factory=factory)
  report=replay.execute(out,client_factory=factory)
  self.assertEqual(report['status'],'passed');self.assertEqual(report['reported_model_calls_known'],0)
  self.assertFalse(report['independent_acceptance']);self.assertEqual(len(self.posts),2)
  self.assertEqual(self.posts[0],self.posts[1]);self.assertEqual(self.posts[0]['query'],'original wording')
  with self.assertRaises(FileExistsError):replay.execute(out,client_factory=factory)
  self.assertEqual(len(self.posts),2)

 def test_server_or_suite_change_blocks_before_submission(self):
  out=self.prepare(client_factory=self.factory())
  with self.assertRaises(ValueError):replay.execute(out,client_factory=self.factory(changed=True))
  self.assertFalse(self.posts)
  (out/'suite.private.json').write_text('{}')
  with self.assertRaises(ValueError):replay.execute(out,client_factory=self.factory())
  self.assertFalse(self.posts)

 def test_unknown_http_stops_next_question_without_retry_and_count_stays_unknown(self):
  data=suite();data['cases'].append({**data['cases'][0],'id':'Q02'})
  factory=self.factory(model=True,status=503);out=self.prepare(data,client_factory=factory,allow_model=True)
  report=replay.execute(out,client_factory=factory)
  self.assertEqual(len(self.posts),1);self.assertTrue(report['stopped'])
  self.assertEqual(report['records'][1]['status'],'not_run_after_stop')
  self.assertEqual(report['responses_with_unknown_model_count'],1)
  self.assertIsNone(report['records'][0]['reported_model_calls'])

 def test_failed_contract_and_nonzero_replay_are_not_passed(self):
  data=suite();data['cases'][0]['turns'][0]['checks'][0]['equals']='wrong'
  factory=self.factory();out=self.prepare(data,client_factory=factory)
  r=replay.execute(out,client_factory=factory);self.assertEqual(r['status'],'failed');self.assertEqual(len(self.posts),1)
  self.posts.clear();out2=self.root/'second';path=self.root/'second.json';path.write_text(json.dumps(suite()))
  factory=self.factory(model=True,replay_calls=1)
  replay.prepare(path,out2,{'model':'http://127.0.0.1:9999'},self.root,allow_model=True,client_factory=factory)
  r=replay.execute(out2,client_factory=factory)
  self.assertEqual(r['status'],'failed');self.assertEqual(r['reported_model_calls_known'],2)

 def test_provenance_import_and_local_endpoint_guards(self):
  data=suite();data['authorship']['independent_acceptance']=True
  with self.assertRaises(ValueError):replay.validate_suite(data)
  data['authorship']={'kind':'external_unseen_declared','independent_acceptance':False}
  with self.assertRaises(ValueError):replay.validate_suite(data)
  data['authorship'].update(source_sha256='a'*64,reviewer='external reviewer declaration',reviewed_at='2026-10-31')
  replay.validate_suite(data)
  path=self.root/'valid.json';path.write_text(json.dumps(data))
  for label,url in [('../escape','http://127.0.0.1:9999'),('bad','https://example.com'),('bad','http://user@127.0.0.1:9999')]:
   with self.assertRaises(ValueError):replay.prepare(path,self.root/'bad',{label:url},self.root,client_factory=self.factory())

 def test_original_json_pointer_and_hash_are_checked(self):
  file=self.root/'raw.json';file.write_text(json.dumps({'a/b':['raw']}))
  info={'relative_file_path':'raw.json','json_pointer':'/a~1b/0','raw_value':'raw','file_sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
  replay.original_pointer(self.root,info)
  with self.assertRaises(ValueError):replay.original_pointer(self.root,{**info,'raw_value':'invented'})
  with self.assertRaises(ValueError):replay.original_pointer(self.root,{**info,'relative_file_path':'../escape.json'})

 def test_actual_xlsx_and_parent_cells_are_verified(self):
  from tests.dataops_fixture import make_sources
  from tcm.dataops.research_contracts import ResearchGoal
  from tcm.dataops.research_graph import ResearchEngine
  from tcm.dataops.research_model import ResearchFixed
  from tcm.dataops.workspace import Workspaces
  _,sources=make_sources(self.root/'source');store=Workspaces(self.root/'work');docs=[]
  for i,fields in enumerate((['功能与主治','注意'],['功能与主治','注意','贮藏'])):
   goal=ResearchGoal(query={'names':['Alpha','Beta'],'limit':20},attributes=['medicinal_part'],document_fields=fields,
                     research={'mode':'audit_named','target_count':2})
   parent=docs[0] if i else None
   with sources() as s:d,_=store.reserve_agent('owner',str(i),goal.model_dump(),s.revision,question='fixture',parent_id=parent['id'] if parent else None,expected_revision=1 if parent else None)
   docs.append(ResearchEngine(store,'owner',d['id'],sources,ResearchFixed()).run())
  turns=[{'query':'first','task':{'mode':'audit_named','fields':['功能与主治','注意'],'rows':['Alpha','Beta'],'missing':['注意']}},
    {'query':'second','task':{'mode':'audit_named','fields':['功能与主治','注意','贮藏'],'rows':['Alpha','Beta'],'preserve_parent':True}}]
  factory=self.factory(docs=docs,store=store);out=self.prepare(suite(turns),client_factory=factory)
  result=replay.execute(out,client_factory=factory);self.assertEqual(result['status'],'passed',result)
  self.assertEqual(len(list(out.rglob('delivery.xlsx'))),2)
  self.assertTrue(result['records'][1]['delivery']['parent_preserved'])
  second=self.root/'tampered';path=self.root/'tampered.json';path.write_text(json.dumps(suite(turns)))
  factory=self.factory(docs=docs,store=store,tamper=True)
  replay.prepare(path,second,{'local':'http://127.0.0.1:9999'},self.root,client_factory=factory)
  r=replay.execute(second,client_factory=factory);self.assertEqual(r['status'],'failed')
  self.assertEqual(r['records'][1]['status'],'not_run_parent_failed')


if __name__=='__main__':unittest.main()
