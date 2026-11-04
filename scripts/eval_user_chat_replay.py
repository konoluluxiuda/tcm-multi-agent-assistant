"""Frozen public-HTTP chat replay; imported external questions retain provenance.

No question rewriting, automatic HTTP retry, model judge, or independent-user claim.
Raw responses and downloads are private; summaries contain checks and counts only.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

ROOT=Path(__file__).resolve().parents[1]


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
 with Path(path).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
 Path(path).chmod(0o600)


def pointer(value,path):
 if path=='':return value
 if not isinstance(path,str) or not path.startswith('/'):raise ValueError('Expected JSON pointer')
 for token in path.split('/')[1:]:
  token=token.replace('~1','/').replace('~0','~')
  value=value[int(token)] if isinstance(value,list) else value[token]
 return value


def validate_suite(suite):
 authorship=suite['authorship'];kind=authorship['kind']
 if kind not in ('observed_user_exposed','external_unseen_declared','developer_authored_frozen'):raise ValueError('Question provenance required')
 if authorship.get('independent_acceptance') is not False:raise ValueError('Runner cannot certify independent acceptance')
 if kind=='external_unseen_declared' and not all(authorship.get(k) for k in ('source_sha256','reviewer','reviewed_at')):
  raise ValueError('External question/reference review declaration required')
 cases=suite['cases'];ids=[c['id'] for c in cases]
 if not 1<=len(ids)<=30 or len(ids)!=len(set(ids)):raise ValueError('Invalid case IDs')
 import re
 for case in cases:
  if not re.fullmatch(r'[A-Za-z0-9_-]{1,30}',case['id']) or case['role'] not in ('business','boundary'):raise ValueError('Invalid case')
  if not 1<=len(case['turns'])<=3:raise ValueError('Invalid scenario length')
  for turn in case['turns']:
   if not isinstance(turn['query'],str) or not 1<=len(turn['query'])<=1000:raise ValueError('Invalid question')
   if not (turn.get('checks') or turn.get('task') or turn.get('no_workspace') or turn.get('relation_requirements') or turn.get('answer')):raise ValueError('A frozen acceptance contract is required')
   if turn.get('answer'):
    answer=turn['answer']
    from scripts.query_answer_checks import validate as validate_answer
    validate_answer(answer)
    sources={v['source'] for v in answer.get('attributes',[])}
    sources.update(n for v in answer.get('relations',[]) for n in v['sources'])
    if not sources<=set(turn.get('reference_sources',[])):raise ValueError('Answer sources must be frozen')
   for name in turn.get('relation_sources',[])+turn.get('reference_sources',[]):
    if not re.fullmatch('[A-Za-z0-9]+',name):raise ValueError('Invalid source basename')


def runtime(client):
 d=client.get('/api/runtime').json();r=d['dataops']['research_selection']
 if d['restart_required'] or d['source_revision']!=d['disk_revision']:raise ValueError('Server source drift')
 if not r['enabled'] or r['driver']!='fixed' or r['execution_model_enabled'] or d['intent_model_enabled']:
  raise ValueError('Only fixed execution with bounded optional understanding is supported')
 readiness={}
 for name in ('herbs','research'):
  response=client.get('/ready/'+name);response.raise_for_status();readiness[name]=response.json()
  if readiness[name].get('status')!='ready':raise ValueError('Required source is not ready')
 d['data_readiness_snapshot']=readiness
 return d


def prepare(suite_path,out,services,source,*,allow_model=False,model_cap=8,client_factory=httpx.Client):
 import re
 suite=json.loads(Path(suite_path).read_text());validate_suite(suite)
 if not services or len(services)>2 or not 0<=model_cap<=30:raise ValueError('Invalid service or request limit')
 servers={}
 for label,url in services.items():
  if not re.fullmatch(r'[A-Za-z0-9_-]{1,30}',label):raise ValueError('Invalid service label')
  parsed=urlsplit(url)
  if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:raise ValueError('Local preview root required')
  with client_factory(base_url=url,timeout=30,follow_redirects=False) as c:servers[label]=runtime(c)
  if servers[label]['dataops']['research_selection']['question_model_enabled'] and (not allow_model or model_cap==0):raise ValueError('Explicit model-enabled batch required')
  selection=servers[label]['dataops']['research_selection']
  if selection['question_model_enabled'] and (selection.get('question_request_limit',1)!=1 or selection.get('question_policy')=='unified_v2'):
   raise ValueError('This frozen evaluator only supports one-request understanding; prepare a separately budgeted unified_v2 protocol')
 if len({d['source_revision'] for d in servers.values()})!=1:raise ValueError('Services do not share research source revision')
 if len({json.dumps(d['data_readiness_snapshot'],sort_keys=True) for d in servers.values()})!=1:raise ValueError('Services do not share source readiness versions')
 out.mkdir(parents=True,exist_ok=False,mode=0o700)
 source=Path(source).resolve();refs={}
 for case in suite['cases']:
  for turn in case['turns']:
   for name in set(turn.get('relation_sources',[])+turn.get('reference_sources',[])):
    path=source/'etcm_herbs'/(name+'.json');refs[str(path)]=digest(path)
 save(out/'suite.private.json',suite)
 for name in ('eval_user_chat_replay.py','query_answer_checks.py'):
  origin=Path(__file__).with_name(name)
  if name=='query_answer_checks.py' and not any(t.get('answer') for c in suite['cases'] for t in c['turns']):continue
  snapshot=out/(name+'.frozen');snapshot.write_bytes(origin.read_bytes());snapshot.chmod(0o600)
 protocol={'version':suite['version'],'suite_sha256':digest(out/'suite.private.json'),'evaluator_sha256':digest(__file__),
  'services':services,'runtimes':servers,'source_root':str(source),'source_sha256':refs,'model_request_cap':model_cap,
  'allow_model':allow_model,'max_requests_per_understanding':1,'automatic_retries':0,
  'authorship':suite['authorship'],'independent_acceptance':False,'scope':'HTTP workflow regression, original-source ETCM sample checks, artifact integrity; not a clinical or extraction-accuracy evaluation',
  'counter_scope':'HTTP-reported requests, not provider wire receipts; unknown counts consume one reserved budget unit',
  'comparison_limit':'Deployed configurations are recorded, not assumed to differ only in model switch.'}
 if any(turn.get('answer') for case in suite['cases'] for turn in case['turns']):
  protocol['answer_evaluator_sha256']=digest(Path(__file__).with_name('query_answer_checks.py'))
 save(out/'protocol.private.json',protocol);return protocol


def reported_calls(body,server):
 value=body.get('model_calls')
 if type(value) is int and value>=0:return value
 if body.get('routing',{}).get('mode') in ('natural','command'):return 0
 if not server['dataops']['research_selection']['question_model_enabled']:return 0
 return None


def original_pointer(source,info):
 p=(source/info['relative_file_path']).resolve()
 if not p.is_relative_to(source) or not p.is_file() or p.stat().st_size>96*1024*1024:raise ValueError('Invalid raw source path')
 if digest(p)!=info['file_sha256'] or pointer(json.loads(p.read_text()),info['json_pointer'])!=info['raw_value']:raise ValueError('Raw source witness mismatch')


def source_checks(turn,body,source):
 checked=0
 if turn.get('herb_provenance'):
  for r in body['tcm']['records']:
   for field in r['fields'].values():
    if field.get('state')=='present':original_pointer(source,field['source']);checked+=1
  if not checked:raise ValueError('Missing field provenance')
 if turn.get('relation_sources'):
  from scripts.eval_local_usability import table_rows
  docs=[json.loads((source/'etcm_herbs'/(n+'.json')).read_text()) for n in turn['relation_sources']]
  kind=turn['relation_kind'];payload=body['assistant']['relations'];data=payload['data']
  expected=len(table_rows(docs[0],'ingredient')) if kind=='ingredient' else len(set.intersection(*[
    {i for row in table_rows(d,'target') for i in row.get('UniProt Accession Number',[])} for d in docs]))
  if data['total']!=expected or not data['items']:raise ValueError('Relation source count mismatch')
  for item in data['items']:
   witnesses=[(0,{'source_pointer':item['source_pointer'],'raw_row':item['raw_row']})] if kind=='ingredient' else [(i,w) for i,s in enumerate(item['sources']) for w in s['witnesses']]
   for index,w in witnesses:
    info=payload['groups'][index]['options'][0]['source']
    original_pointer(source,{**info,'json_pointer':w['source_pointer'],'raw_value':w['raw_row']});checked+=1
    if kind=='common_target' and item['identifier'] not in w['raw_row']['UniProt Accession Number']:raise ValueError('Wrong target witness')
 return checked


def relation_bundle(body, requirements):
 """Require each requested source/kind, rather than scoring a partial answer OK."""
 found=set()
 def visit(value):
  if isinstance(value,dict):
   if value.get('kind') in ('ingredient','target') and isinstance(value.get('data'),dict):
    data=value['data']
    if isinstance(data.get('total'),int) and (data['total']==0 or data.get('items')):
     for group in value.get('groups',[]):
      for option in group.get('options',[]):
       path=option.get('source',{}).get('relative_file_path','')
       found.add((value['kind'],Path(path).stem))
   for child in value.values():visit(child)
  elif isinstance(value,list):
   for child in value:visit(child)
 visit(body)
 missing=[r for r in requirements if (r['kind'],r['source']) not in found]
 if missing:raise ValueError('Not all requested source/kind result groups were delivered')


def check_task(client,body,expect,folder,parent=None,timeout=90):
 from tcm.dataops.artifacts import verify
 identifier=body['assistant']['workspace_id'];deadline=time.monotonic()+timeout
 while True:
  snapshot=client.get('/api/dataops/agent-runs/'+identifier).json();doc=snapshot['workspace']
  if snapshot['job']['status'] not in ('queued','running'):break
  if time.monotonic()>=deadline:raise ValueError('Task polling timeout')
  time.sleep(.2)
 save(folder/'workspace.private.json',doc)
 if doc['status'] not in expect.get('allowed_status',['complete']) or not doc.get('artifact'):raise ValueError('Task did not deliver')
 if doc['goal']['research']['mode']!=expect['mode'] or set(doc['goal']['document_fields'])!=set(expect['fields']):raise ValueError('Task goal mismatch')
 if 'attributes' in expect and set(doc['goal']['attributes'])!=set(expect['attributes']):raise ValueError('Task attribute set mismatch')
 rows=doc['result']['rows']
 if sorted(r['name'] for r in rows)!=sorted(expect['rows']):raise ValueError('Task row set mismatch')
 for field in expect.get('missing',[]):
  if not all(r['cells'][field]['status']=='missing' for r in rows):raise ValueError('Missing data was filled')
 content=client.get('/api/dataops/workspaces/'+identifier+'/artifact');content.raise_for_status()
 path=folder/'delivery.xlsx';path.write_bytes(content.content);path.chmod(0o600)
 if digest(path)!=doc['artifact']['sha256']:raise ValueError('Download checksum mismatch')
 verification=verify(path,doc['result'])
 if expect.get('preserve_parent'):
  if not parent or doc['parent_id']!=parent[0]['id'] or doc['revision']!=parent[0]['revision']+1:raise ValueError('Parent identity changed')
  old,parent_path=parent;by_id={r['source_id']:r for r in rows}
  if set(by_id)!={r['source_id'] for r in old['result']['rows']}:raise ValueError('Parent row set changed')
  if any(by_id[r['source_id']]['cells'][f]!=v for r in old['result']['rows'] for f,v in r['cells'].items()):raise ValueError('Parent cell changed')
  old_content=client.get('/api/dataops/workspaces/'+old['id']+'/artifact');old_content.raise_for_status()
  if hashlib.sha256(old_content.content).hexdigest()!=digest(parent_path):raise ValueError('Old artifact changed')
 return (doc,path),{'rows':len(rows),'verification':verification,'parent_preserved':bool(expect.get('preserve_parent'))}


def read_query_result(client,identifier,folder,timeout=90):
 """Read already delivered observations; never query sources or call a model."""
 import re
 if not re.fullmatch('[0-9a-f]{32}',identifier):raise ValueError('Invalid query task ID')
 deadline=time.monotonic()+timeout
 while True:
  terminal=client.get('/api/research/runs/'+identifier);terminal.raise_for_status();payload=terminal.json()
  if payload.get('status') not in ('queued','running'):break
  if time.monotonic()>=deadline:raise ValueError('Query task timed out')
  time.sleep(.2)
 save(folder/'query-task.private.json',payload)
 if payload.get('status') not in ('complete','partial','ok'):raise ValueError('Query task did not complete')
 references=(payload.get('result') or {}).get('run',{}).get('evidence',{})
 if len(references)>8:raise ValueError('Too many query observations')
 observations=[]
 for evidence_id,reference in references.items():
  if not re.fullmatch('obs-[1-9][0-9]*',evidence_id):raise ValueError('Invalid query evidence ID')
  response=client.get('/api/research/runs/'+identifier+'/evidence/'+evidence_id+'/download');response.raise_for_status()
  if len(response.content)>16*1024*1024:raise ValueError('Query observation exceeds evaluation budget')
  if reference.get('content_sha256') and hashlib.sha256(response.content).hexdigest()!=reference['content_sha256']:
   raise ValueError('Downloaded query observation checksum mismatch')
  full=response.json();save(folder/('query-evidence-'+evidence_id+'.private.json'),full);observations.append(full)
 return {'task':payload,'observations':observations}


def execute(out,*,client_factory=httpx.Client):
 protocol=json.loads((out/'protocol.private.json').read_text())
 if digest(__file__)!=protocol['evaluator_sha256'] or digest(out/'suite.private.json')!=protocol['suite_sha256']:raise ValueError('Frozen evaluator/suite changed')
 if protocol.get('answer_evaluator_sha256') and digest(Path(__file__).with_name('query_answer_checks.py'))!=protocol['answer_evaluator_sha256']:
  raise ValueError('Frozen answer evaluator changed')
 for name,sha in protocol['source_sha256'].items():
  if digest(name)!=sha:raise ValueError('Frozen source changed')
 suite=json.loads((out/'suite.private.json').read_text());clients={}
 try:
  for label,url in protocol['services'].items():
   c=client_factory(base_url=url,timeout=30,follow_redirects=False);clients[label]=c
   if runtime(c)!=protocol['runtimes'][label]:raise ValueError('Frozen service changed or restarted')
  save(out/'execution_started.json',{'consumed_even_if_interrupted':True})
  records=[];stopped=False;reserved=0;known=0;unknown=0
  for label,client in clients.items():
   server=protocol['runtimes'][label]
   for case in suite['cases']:
    parent=None
    client.cookies.clear();client.get('/dataops/research')
    for index,turn in enumerate(case['turns']):
     folder=out/(label+'_'+case['id']+'_'+str(index));folder.mkdir()
     row={'service':label,'id':case['id'],'turn':index,'role':case['role'],'passed':False}
     if stopped:
      records.append({**row,'status':'not_run_after_stop'});continue
     if turn.get('task',{}).get('preserve_parent') and parent is None:
      records.append({**row,'status':'not_run_parent_failed'});continue
     enabled=server['dataops']['research_selection']['question_model_enabled']
     if enabled and reserved>=protocol['model_request_cap']:
      records.append({**row,'status':'not_run_budget'});stopped=True;continue
     started=time.monotonic();body={'query':turn['query'],'request_key':uuid4().hex};calls=None;value={};request_id=None
     try:
      response=client.post('/chat',json=body)
      if response.status_code in (429,503) or response.status_code>=500:stopped=True
      request_id=response.headers.get('x-request-id')
      value=response.json();save(folder/'response.private.json',{'http':response.status_code,'body':value,'request_id':request_id})
      row['request_id']=request_id
      calls=reported_calls(value,server);known+=calls or 0;unknown+=calls is None;reserved+=(calls if calls is not None else 1) if enabled else 0
      row.update(http=response.status_code,reported_model_calls=calls)
      if calls is not None and calls>1:raise ValueError('Single-understanding request contract exceeded')
      if response.status_code in (429,503) or response.status_code>=500:
       stopped=True;raise ValueError('Busy or unknown dispatch; batch did not retry')
      if response.status_code not in turn.get('allowed_http',[turn.get('http',200)]):raise ValueError('Unexpected HTTP status')
      if turn.get('safe_rejection') and response.status_code!=403 and value.get('assistant',{}).get('status') not in ('needs_input','clarification_needed','unsupported'):
       raise ValueError('Unsupported or unclear request was not safely clarified/rejected')
      if turn.get('relation_requirements'):
       if value.get('assistant',{}).get('status') in ('queued','running') and value['assistant'].get('run_id'):
        deadline=time.monotonic()+90
        while True:
         terminal=client.get('/api/research/runs/'+value['assistant']['run_id']);terminal.raise_for_status()
         payload=terminal.json()
         if payload.get('status') not in ('queued','running'):break
         if time.monotonic()>=deadline:raise ValueError('Relation bundle task timed out')
         time.sleep(.3)
        save(folder/'relation-task.private.json',payload)
        relation_bundle(payload,turn['relation_requirements'])
       else:relation_bundle(value,turn['relation_requirements'])
      for check in turn.get('checks',[]):
       if pointer(value,check['path'])!=check['equals']:raise ValueError('Response assertion failed: '+check['path'])
      if turn.get('answer'):
       from scripts.query_answer_checks import check as check_answer
       answer_value={'chat':value};assistant=value.get('assistant',{})
       if assistant.get('run_id'):
        answer_value.update(read_query_result(client,assistant['run_id'],folder))
       row['answer']=check_answer(answer_value,turn['answer'],protocol['source_root'])
      row['raw_source_witnesses_checked']=source_checks(turn,value,Path(protocol['source_root']))
      if turn.get('task'):
       parent,receipt=check_task(client,value,turn['task'],folder,parent)
       row['delivery']=receipt
      if turn.get('no_workspace') and client.get('/api/dataops/workspaces').json()['items']:raise ValueError('Unexpected workspace for boundary')
      row['primary_contract_passed']=True
      if response.status_code<400:
       if enabled and reserved>=protocol['model_request_cap']:
        row['replay_status']='not_run_no_budget'
       else:
        replay=client.post('/chat',json=body)
        if replay.status_code==429 or replay.status_code>=500:stopped=True
        replayed=replay.json();save(folder/'replay.private.json',{'http':replay.status_code,'body':replayed})
        extra=reported_calls(replayed,server);known+=extra or 0;unknown+=extra is None;reserved+=(extra if extra is not None else 1) if enabled else 0
        if replay.status_code!=response.status_code or extra!=0:raise ValueError('Replay changed status or model count')
        if turn.get('task') and replayed['assistant']['workspace_id']!=parent[0]['id']:raise ValueError('Replay created another workspace')
        row.update(replay_added_model_calls=extra,replay_status='passed')
      row.update(status='passed',passed=True)
     except (ValueError,KeyError,TypeError,IndexError,httpx.HTTPError) as exc:
      row.update(status='failed',reason=str(exc)[:180])
      if enabled and 'reported_model_calls' not in row:
       row['reported_model_calls']=None;unknown+=1;reserved+=1
      if isinstance(exc,httpx.HTTPError):stopped=True
     if request_id:
      try:
       diagnostic=client.get('/api/dataops/diagnostics/'+request_id)
       row['diagnostic_http']=diagnostic.status_code
       if diagnostic.is_success:
        detail=diagnostic.json();save(folder/'diagnostic.private.json',detail)
        row['diagnostic_reasons']=[{'stage':e['stage'],'status':e['status'],'code':e.get('code')}
           for e in detail.get('events',[]) if e.get('code') or e['status'] in ('rejected','needs_input','failed')]
      except (httpx.HTTPError,ValueError,KeyError):row['diagnostic_readback']='unavailable'
     row['duration_seconds']=round(time.monotonic()-started,3)
     records.append(row);save(folder/'score.json',row)
  by_service={label:{role:{'passed':sum(r['passed'] for r in records if r['service']==label and r['role']==role),
   'total':sum(r['service']==label and r['role']==role for r in records)} for role in ('business','boundary')} for label in clients}
  skipped_replays=sum(r.get('replay_status')=='not_run_no_budget' for r in records)
  report={'version':protocol['version'],'status':('partial' if skipped_replays else 'passed') if all(r['passed'] for r in records) else 'failed',
   'records':records,'by_service':by_service,'reported_model_calls_known':known,'responses_with_unknown_model_count':unknown,
   'reserved_model_units':reserved,'stopped':stopped,'authorship':protocol['authorship'],'independent_acceptance':False,
   'raw_values_published':False,'model_count_scope':protocol['counter_scope'],'comparison_limit':protocol['comparison_limit'],
   'replay_checks_not_run':skipped_replays}
  save(out/'report.json',report);return report
 finally:
  for c in clients.values():c.close()


if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--execute',action='store_true');parser.add_argument('--suite',type=Path,default=ROOT/'eval/user_chat_replay_v1.json');parser.add_argument('--source',type=Path,default=ROOT.parent/'HDCTI/dataset/ETCM2.0');parser.add_argument('--service',action='append',default=[]);parser.add_argument('--allow-model',action='store_true');parser.add_argument('--model-cap',type=int,default=8);args=parser.parse_args()
 result=execute(args.out) if args.execute else prepare(args.suite,args.out,dict(v.split('=',1) for v in args.service),args.source,allow_model=args.allow_model,model_cap=args.model_cap)
 print(json.dumps({k:result[k] for k in ('version','status','by_service','reported_model_calls_known','responses_with_unknown_model_count','model_request_cap') if k in result},ensure_ascii=False))
