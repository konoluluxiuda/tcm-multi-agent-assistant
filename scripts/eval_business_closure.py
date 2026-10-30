"""Freeze and screen actual local business tasks; never claims independent accuracy."""
import argparse
from collections import Counter
from datetime import datetime,timezone
from fractions import Fraction
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
HERBS={'BaiZhu':'白术','HuangQi':'黄芪','DangGui':'当归','ChuanXiong':'川芎'}
FIELDS={'family':('Family','科'),'medicinal_part':('Medicinal Part','药用部位')}
DISEASE='etcm-disease:482d905401f31766a0cbf04b59a11a27dfeb462897e369d7f1aa2fcace441a59'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def write(path,value):
    with Path(path).open('x',encoding='utf-8') as f:
        os.chmod(path,0o600);json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')


def ro(path):return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)


def walk(value):
    if isinstance(value,dict):
        yield value
        for v in value.values():yield from walk(v)
    elif isinstance(value,list):
        for v in value:yield from walk(v)


def target_set(document):
    result=set()
    for section in document.get('data',[]):
        if section.get('id')!='related_table':continue
        for table in section.get('value',[]):
            if table.get('type')!='target':continue
            for row in table.get('value',[]):
                value=row.get('UniProt Accession Number')
                value=value[0] if isinstance(value,list) and len(value)==1 else value
                if isinstance(value,str) and re.fullmatch(r'[A-Z0-9]{6}(?:[A-Z0-9]{4})?',value.strip()):result.add(value.strip())
    return sorted(result)


def ranking(query,sets,k=5):
    query=set(query);rows=[]
    for sid,values in sets.items():
        values=set(values);shared=len(query&values)
        if shared:rows.append((sid,shared,len(query|values)))
    return [sid for sid,shared,total in sorted(rows,key=lambda r:(-Fraction(r[1],r[2]),-r[1],r[0]))[:k]]


def references(args):
    refs={'source_root':str(args.source.resolve()),'herbs':{},'chunks':{},'rankings':{},'inputs':{}}
    paths=[args.herbs,args.enrichment,args.pharma/'research.sqlite3',args.pharma/'manifest.json',args.diseases]
    with ro(args.herbs) as db:
        refs['herb_records']=db.execute("SELECT count(*) FROM records WHERE state='valid' AND dataset_version='local-2026-10-10'").fetchone()[0]
        for name,zh in HERBS.items():
            relative='etcm_herbs/'+name+'.json';path=args.source/relative;paths.append(path)
            rows=db.execute("SELECT source_record_id,sha256 FROM records WHERE relative_path=? AND state='valid' AND dataset_version='local-2026-10-10'",(relative,)).fetchall()
            if len(rows)!=1 or sha(path)!=rows[0][1]:raise ValueError('Original herb snapshot mismatch')
            doc=json.loads(path.read_text());fields={}
            for s in doc['data']:
                if s.get('id')=='base_information':
                    for field in s['value']:fields[field['key']]=field.get('value')
            refs['herbs'][name]={'name_zh':zh,'id':rows[0][0],'relative':relative,'sha256':rows[0][1],
                                 'targets':target_set(doc),'fields':fields}
    with ro(args.pharma/'research.sqlite3') as db:
        refs['pharma_entries']=db.execute('SELECT count(*) FROM entries').fetchone()[0]
        for cid,name,label,raw,payload in db.execute('SELECT chunk_id,name,label,raw_text,payload FROM chunks WHERE name IN (?,?,?,?)',tuple(HERBS.values())):
            refs['chunks'][cid]={'name':name,'label':label,'raw_text':raw,'evidence':json.loads(payload)['evidence']}
    sets={}
    with ro(args.enrichment) as db:
        for sid,uid in db.execute("SELECT DISTINCT e.herb_id,n.identifier FROM edges e JOIN nodes n USING(node_id) WHERE e.kind='target' AND n.kind='target' AND n.identity_status LIKE 'source_declared_uniprot_not_externally_verified%'"):
            sets.setdefault(sid,[]).append(uid)
    for name,h in refs['herbs'].items():
        if set(sets.get(h['id'],[]))!=set(h['targets']):raise ValueError('Original/index target sets differ')
        refs['rankings'][name]=ranking(h['targets'],{s:v for s,v in sets.items() if s!=h['id']})
    with ro(args.diseases) as db:
        row=db.execute('SELECT name,document FROM diseases WHERE record_id=?',(DISEASE,)).fetchone()
        doc=json.loads(row[1]);source=doc['source'];path=args.source/source['relative_path'];paths.append(path)
        if sha(path)!=source['file_sha256']:raise ValueError('Original disease snapshot mismatch')
        raw=json.loads(path.read_text());targets=target_set(raw)
        refs['disease']={'id':DISEASE,'name':row[0],'source':source,'targets':targets,
            'row_count':db.execute('SELECT count(*) FROM target_rows WHERE record_id=?',(DISEASE,)).fetchone()[0],
            'ranking':ranking(targets,sets)}
        refs['directory']={sid:name for sid,name in db.execute('SELECT record_id,name FROM diseases')}
    refs['inputs']={str(p.resolve()):sha(p) for p in paths}
    return refs


def validate_suite(suite):
    if suite.get('independent_acceptance') is not False:raise ValueError('Independent custody not established')
    cases=suite['cases']
    if len(cases)!=24 or len({c['id'] for c in cases})!=24:raise ValueError('Expected fixed 24-case screen')
    for c in cases:
        if c['role'] not in ('positive_task','boundary') or not 1<=len(c['turns'])<=2:raise ValueError('Invalid case')
        if c['role']=='positive_task' and not c['goals']:raise ValueError('Positive goal missing')


def freeze(args):
    suite=json.loads(args.suite.read_text());validate_suite(suite);refs=references(args)
    for case in suite['cases']:
        case['turns']=[q.format(disease_id=refs['disease']['id'],disease_name=refs['disease']['name']) for q in case['turns']]
    previous=set()
    for file in (ROOT/'eval').glob('*.json'):
        if file.resolve()==args.suite.resolve():continue
        try:other=json.loads(file.read_text())
        except (ValueError,OSError):continue
        for node in walk(other):
            previous.update(v for k,v in node.items() if k in ('query','question','setup_query') and isinstance(v,str))
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    write(args.out/'suite.json',suite);write(args.out/'references.json',refs)
    paths={p for folder in ('agents','tcm','templates') for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.js','.html')}
    paths.update(ROOT/p for p in ('app.py','config.py','features.py','scripts/eval_business_closure.py'))
    code={str(p.relative_to(ROOT)):sha(p) for p in sorted(paths)}
    for name in code:
        target=args.out/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target);target.chmod(0o600)
    record={'created_utc':datetime.now(timezone.utc).isoformat(),'scope':suite['scope'],'independent_acceptance':False,
        'human_signoff':False,'suite_sha256':sha(args.out/'suite.json'),'reference_sha256':sha(args.out/'references.json'),
        'source_sha256':code,'inputs':refs['inputs'],'exact_prior_matches':[c['id'] for c in suite['cases'] if any(q in previous for q in c['turns'])],
        'note':'Local freeze by an author aware of code and old templates, not independent custody or a signed timestamp.'}
    write(args.out/'frozen.json',record);print(json.dumps({'status':'frozen','cases':len(suite['cases']),'independent_acceptance':False}))


def verify(folder):
    record=json.loads((folder/'frozen.json').read_text())
    if sha(folder/'suite.json')!=record['suite_sha256'] or sha(folder/'references.json')!=record['reference_sha256']:raise ValueError('Frozen inputs changed')
    for name,digest in record['source_sha256'].items():
        if sha(ROOT/name)!=digest or sha(folder/'source'/name)!=digest:raise ValueError('Frozen code changed: '+name)
    for name,digest in record['inputs'].items():
        if sha(name)!=digest:raise ValueError('Source data changed: '+name)
    return record


def refreeze(args):
    """New runtime snapshot with byte-identical original questions and oracle.

    Preserve the first record and reject changes to the scoring implementation.
    A replay is development evidence, never a second independent first pass.
    """
    import ast
    original=json.loads((args.baseline/'frozen.json').read_text())
    for name,key in (('suite.json','suite_sha256'),('references.json','reference_sha256')):
        if sha(args.baseline/name)!=original[key]:raise ValueError('Original bank or oracle changed')
    for name,digest in original['source_sha256'].items():
        if sha(args.baseline/'source'/name)!=digest:raise ValueError('Original code archive changed')
    for name,digest in original['inputs'].items():
        if sha(name)!=digest:raise ValueError('Original source data changed')
    def graders(path):
        return {node.name:ast.dump(node,include_attributes=False) for node in ast.parse(path.read_text()).body
                if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in ('Judge','assess','summarize')}
    if graders(args.baseline/'source/scripts/eval_business_closure.py')!=graders(Path(__file__)):
        raise ValueError('Scoring changed; requires separate adjudication, not this replay')
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    for name in ('suite.json','references.json'):shutil.copyfile(args.baseline/name,args.out/name);(args.out/name).chmod(0o600)
    paths={p for folder in ('agents','tcm','templates') for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.js','.html')}
    paths.update(ROOT/p for p in ('app.py','config.py','features.py','scripts/eval_business_closure.py'))
    code={str(p.relative_to(ROOT)):sha(p) for p in sorted(paths)}
    for name in code:
        target=args.out/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target);target.chmod(0o600)
    record={**original,'created_utc':datetime.now(timezone.utc).isoformat(),'source_sha256':code,
        'scope':'development_replay_of_known_business_closure_v1','baseline_manifest_sha256':sha(args.baseline/'frozen.json'),
        'grading_ast_unchanged':True,'note':'Known-case repair regression; original question/oracle bytes and scoring AST unchanged. Not independent acceptance.'}
    write(args.out/'frozen.json',record)
    print(json.dumps({'status':'refrozen','scope':record['scope'],'grading_ast_unchanged':True,'independent_acceptance':False}))


def local_url(url):
    parts=urllib.parse.urlsplit(url)
    if parts.scheme!='http' or parts.hostname!='127.0.0.1' or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('Only explicit local preview origins accepted')
    return url.rstrip('/')


def attest(url):
    port=str(urllib.parse.urlsplit(local_url(url)).port or 80);matches=[]
    for path in Path('/proc').iterdir():
        try:
            parts=(path/'cmdline').read_bytes().split(b'\0')
            if b'scripts.preview_local_tasks' in parts and b'--port' in parts and parts[parts.index(b'--port')+1].decode()==port:
                if b'--with-intent-model' in parts:raise ValueError('Model-enabled preview rejected')
                matches.append(int(path.name))
        except (OSError,IndexError):continue
    if len(matches)!=1:raise ValueError('Need exactly one owned rule-only preview process')
    return {'pid':matches[0],'port':port,'launch':'scripts.preview_local_tasks without intent model','external_model_calls':0}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Evaluation redirects forbidden')


class Client:
    def __init__(self,url):
        self.url=local_url(url);self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self,path,body=None):
        data=None if body is None else json.dumps(body).encode()
        request=urllib.request.Request(self.url+path,data=data,headers={'Content-Type':'application/json'})
        try:
            with self.http.open(request,timeout=90) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as exc:return exc.code,json.load(exc)


class GoalNotMet(Exception):pass
def need(condition,message):
    if not condition:raise GoalNotMet(message)


class Judge:
    def __init__(self,refs):self.refs=refs;self.originals={};self.proofs=0
    def pointer(self,source,pointer,value):
        relative=source.get('relative_file_path',source.get('relative_path'));need(bool(relative) and bool(pointer),'missing source pointer')
        path=(Path(self.refs['source_root'])/relative).resolve()
        need(path.is_relative_to(Path(self.refs['source_root']).resolve()),'source outside root')
        if relative not in self.originals:self.originals[relative]=(sha(path),json.loads(path.read_text()))
        digest,raw=self.originals[relative];need(digest==source.get('file_sha256'),'source SHA mismatch')
        for token in pointer.split('/')[1:]:
            token=token.replace('~1','/').replace('~0','~');raw=raw[int(token)] if isinstance(raw,list) else raw[token]
        need(raw==value,'original JSON row mismatch');self.proofs+=1
    def goal(self,goal,body,payloads):
        nodes=list(walk([body,*payloads]));kind=goal['kind'];assistant=body.get('assistant',{})
        if kind=='overview':
            need(assistant.get('topic')=='overview','overview intent missing')
            need(assistant.get('herbs',{}).get('source_record_count')==self.refs['herb_records'],'herb count mismatch')
            need(assistant.get('pharmacopoeia',{}).get('entry_count')==self.refs['pharma_entries'],'pharma count mismatch');return
        if kind=='dataset_info':
            need(assistant.get('topic')=='dataset_info','database explanation not returned')
            need(set(goal['ids'])<={d.get('id') for d in assistant.get('datasets',[])},'database explanation missing');return
        if kind=='text':
            need(all(t in body.get('response','') for t in goal['contains']),'requested explanation missing');return
        if kind=='pharma_fields':
            observed=set()
            for hit in nodes:
                if hit.get('name')!=goal['name'] or 'raw_text' not in hit or 'chunk_id' not in hit:continue
                original=self.refs['chunks'].get(hit['chunk_id']);need(original is not None,'unknown pharma chunk')
                need(all(hit.get(k)==original[k] for k in ('name','label','raw_text','evidence')),'pharma source differs')
                label={'性味':'性味与归经','origin':'来源'}.get(hit.get('label'),hit.get('label'));observed.add(label);self.proofs+=1
            need(set(goal['fields'])<=observed,'requested pharma fields not found');return
        if kind=='herb_fields':
            herb=self.refs['herbs'][goal['herb']];observed=set()
            for node in nodes:
                if node.get('source_record_id')==herb['id'] and isinstance(node.get('fields'),dict):
                    for field in goal['fields']:
                        value=node['fields'].get(field,{})
                        if value.get('state')=='present':
                            source=value.get('source',{});self.pointer(source,source.get('json_pointer'),source.get('raw_value'))
                            need(source.get('raw_value')==herb['fields'][FIELDS[field][0]],'wrong herb field');observed.add(field)
                if node.get('source_record_id')==herb['id'] and isinstance(node.get('rows'),list):
                    for row in node['rows']:
                        for field in goal['fields']:
                            if row.get('label')==FIELDS[field][1]:
                                source=row.get('source',{});self.pointer(source,source.get('json_pointer'),row.get('original_value'))
                                need(row.get('original_value')==herb['fields'][FIELDS[field][0]],'wrong attribute field');observed.add(field)
            need(set(goal['fields'])<=observed,'requested ETCM fields not found');return
        if kind=='shared':
            herbs=[self.refs['herbs'][h] for h in goal['herbs']];wanted=set(herbs[0]['targets'])&set(herbs[1]['targets'])
            candidates=[n for n in nodes if n.get('total')==len(wanted) and isinstance(n.get('items'),list) and n['items'] and all('identifier' in i and 'sources' in i for i in n['items'])]
            need(candidates,'shared target evidence missing');data=max(candidates,key=lambda n:len(n['items']))
            expected=sorted(wanted) if goal['all_targets'] else sorted(wanted)[:20]
            need([i['identifier'] for i in data['items']]==expected,'shared target set/page incomplete')
            for item in data['items']:
                need({s['source_record_id'] for s in item['sources']}=={h['id'] for h in herbs},'wrong pair source')
                for source in item['sources']:
                    herb=next(h for h in herbs if h['id']==source['source_record_id']);need(source.get('witnesses'),'missing pair witness')
                    for row in source['witnesses']:
                        self.pointer({'relative_file_path':herb['relative'],'file_sha256':herb['sha256']},row['source_pointer'],row['raw_row'])
                        need(item['identifier'] in row['raw_row'].get('UniProt Accession Number',[]),'wrong witness identifier')
            return
        data_nodes=[n for n in nodes if isinstance(n.get('data'),dict)]
        if kind in ('profile','similarity'):
            herb=self.refs['herbs'][goal['herb']]
            matches=[n['data'] for n in data_nodes if n['data'].get('profile',{}).get('source',{}).get('source_record_id')==herb['id']]
            need(matches,'target profile missing');data=matches[0];need(set(data['profile'].get('target_ids',[]))==set(herb['targets']),'wrong target profile')
            if kind=='similarity':
                need(data.get('metric')=='jaccard' and data.get('top_k')==goal['top_k'],'wrong ranking request')
                need([i['source_record_id'] for i in data.get('items',[])]==self.refs['rankings'][goal['herb']][:goal['top_k']],'wrong source ranking')
            return
        if kind=='directory':
            matches=[n['data'] for n in data_nodes if n.get('tool')=='search_disease_sources'];need(matches,'disease directory missing')
            items=matches[0].get('items',[]);need(len(items)==goal['count'],'wrong directory size')
            need(all(self.refs['directory'].get(i.get('record_id'))==i.get('name') for i in items),'wrong disease ID/name');return
        if kind in ('disease_profile','disease_rank'):
            disease=self.refs['disease'];name='disease_gene_profile' if kind=='disease_profile' else 'rank_herbs_for_gene_set'
            matches=[n['data'] for n in data_nodes if n.get('tool')==name and n['data'].get('record_id')==disease['id']]
            need(matches,'disease task evidence missing')
            if kind=='disease_rank':need([i['source_record_id'] for i in matches[0].get('items',[])]==disease['ranking'][:goal['top_k']],'wrong disease candidate ranking')
            else:
                need(set(matches[0].get('target_ids',[]))==set(disease['targets']),'wrong disease target set')
                unique={r['json_pointer']:r for d in matches for r in d.get('rows',[])}
                if goal.get('all_rows'):need(len(unique)==disease['row_count'],'disease rows incomplete')
                for pointer,row in unique.items():self.pointer(disease['source'],pointer,row['raw_row'])
            return
        raise ValueError('Unknown scoring goal '+kind)


def assess(case,status,body,payloads,judge):
    if case['role']=='boundary':
        if case.get('boundary')=='pubmed_disabled':
            return {'outcome':'tool_unavailable' if status==503 and body.get('pubmed',{}).get('status')=='pubmed_disabled' else 'boundary_failed'}
        facts=bool(payloads) or any(n.get('hits') or n.get('items') or n.get('records') for n in walk(body))
        valid=status==403 or (status==200 and not facts and (body.get('assistant',{}).get('status') in ('clarification_needed','unsupported','unavailable') or body.get('research',{}).get('status') in ('name_not_resolved','field_not_resolved','clarify_entity','not_found','unsupported_query')))
        return {'outcome':'appropriate_boundary' if valid and not facts else 'boundary_failed'}
    if status==403:return {'outcome':'unsupported_intent','reason':'unchanged original question not recognized with model fallback disabled'}
    if status>=400:return {'outcome':'technical_failure','reason':'HTTP '+str(status)}
    assistant=body.get('assistant',{})
    if assistant.get('status') in ('partial','clarification_needed','unavailable','busy'):
        return {'outcome':'partial' if assistant['status']=='partial' else 'clarification','reason':assistant.get('message','')}
    before=judge.proofs
    try:
        for goal in case['goals']:judge.goal(goal,body,payloads)
        return {'outcome':'complete','source_checks':judge.proofs-before,'human_semantic_review':'pending'}
    except GoalNotMet as exc:return {'outcome':'requirements_not_met','reason':str(exc),'source_checks':judge.proofs-before}
    except (KeyError,TypeError,IndexError) as exc:return {'outcome':'grader_error','reason':type(exc).__name__+': '+str(exc)}


def summarize(rows):
    positive=[r for r in rows if r['role']=='positive_task'];boundary=[r for r in rows if r['role']=='boundary']
    done=sum(r['outcome']=='complete' for r in positive)
    return {'status':'completed' if done==len(positive) and all(r['outcome'] in ('appropriate_boundary','tool_unavailable') for r in boundary) else 'completed_with_gaps',
        'positive':{'planned':len(positive),'complete':done,'outcomes':dict(Counter(r['outcome'] for r in positive))},
        'boundary':{'planned':len(boundary),'outcomes':dict(Counter(r['outcome'] for r in boundary))},
        'independent_acceptance':False,'human_signoff':False,'uncollected_independent_cases':40,
        'external_model_calls':0,'provider_tokens':None,'model_api_cost':0,'total_system_cost':None,
        'interpretation':'Author screening on fixed known local data. Boundary handling is not task completion; source checks are not clinical/semantic review.'}


def run(args):
    frozen=verify(args.frozen);runtime=attest(args.url);suite=json.loads((args.frozen/'suite.json').read_text());refs=json.loads((args.frozen/'references.json').read_text())
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700);rows=[]
    for case in suite['cases']:
        client=Client(args.url);client.request('/conversation');turns=[];started=time.monotonic();payloads=[];job=None;first_run=None
        try:
            for number,query in enumerate(case['turns']):
                status,body=client.request('/chat',{'query':query,'request_key':case['id']+'-turn-'+str(number)})
                turns.append({'query':query,'http_status':status,'body':body});assistant=body.get('assistant',{})
                if assistant.get('job_id') and assistant.get('status') in ('queued','running'):
                    identifier=assistant['job_id']
                    for _ in range(300):
                        code,job=client.request('/api/research/jobs/'+identifier)
                        if code!=200:raise ValueError('Job retrieval HTTP '+str(code))
                        if job['status'] not in ('queued','running'):break
                        time.sleep(.2)
                    else:raise TimeoutError('Job did not finish')
                    display=(job.get('result') or {}).get('display')
                    if display:body={**body,'assistant':display,'response':display['message']}
                    else:body={**body,'assistant':{'status':job['status'],'message':job.get('reason')}}
                current=body.get('assistant',{}).get('run_id')
                if number==0:first_run=current
            last=body.get('assistant',{});identifier=last.get('run_id')
            if identifier:
                code,doc=client.request('/api/research/runs/'+identifier)
                if code!=200:raise ValueError('Run retrieval HTTP '+str(code))
                for eid,observation in (doc.get('result') or {}).get('run',{}).get('evidence',{}).items():
                    if observation.get('externalized'):
                        code,observation=client.request('/api/research/runs/'+identifier+'/evidence/'+eid+'/download')
                        if code!=200:raise ValueError('Evidence download HTTP '+str(code))
                    payloads.append(observation)
            judge=Judge(refs);scored=assess(case,status,body,payloads,judge)
            if case.get('require_same_run') and not (first_run and first_run==identifier):scored={'outcome':'requirements_not_met','reason':'followup changed or lost original run'}
            if identifier and scored['outcome']=='complete':
                _,restored=client.request('/conversation')
                if restored.get('assistant',{}).get('run_id')!=identifier:scored={'outcome':'requirements_not_met','reason':'saved run not restored'}
        except (OSError,ValueError,TimeoutError) as exc:scored={'outcome':'technical_failure','reason':type(exc).__name__+': '+str(exc)}
        write(args.out/(case['id']+'.private.json'),{'turns':turns,'last_body':body if turns else None,'observations':payloads,'job':job})
        row={'id':case['id'],'role':case['role'],'origin':case['origin'],'queries':case['turns'],**scored,
            'elapsed_ms':round((time.monotonic()-started)*1000,3),'question_posts':len(turns),'external_model_calls':0}
        rows.append(row);print(json.dumps({k:row[k] for k in ('id','outcome','elapsed_ms')},ensure_ascii=False),flush=True)
    verify(args.frozen)
    if attest(args.url)!=runtime:raise ValueError('Preview process changed during run')
    report={**summarize(rows),'created_utc':datetime.now(timezone.utc).isoformat(),'runtime':runtime,'suite_sha256':frozen['suite_sha256'],
        'source_version_sha256':hashlib.sha256(json.dumps(frozen['source_sha256'],sort_keys=True).encode()).hexdigest(),'cases':rows}
    write(args.out/'summary.json',report);print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
    freeze_parser=sub.add_parser('freeze');freeze_parser.add_argument('--suite',type=Path,default=ROOT/'eval/business_closure_v1.json');freeze_parser.add_argument('--out',type=Path,required=True)
    freeze_parser.add_argument('--source',type=Path,default=ROOT.parent/'HDCTI/dataset/ETCM2.0');freeze_parser.add_argument('--herbs',type=Path,default=ROOT/'var/tcm/herbs.sqlite3')
    freeze_parser.add_argument('--enrichment',type=Path,default=ROOT/'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3');freeze_parser.add_argument('--pharma',type=Path,default=ROOT/'var/mineru_retrieval_v2');freeze_parser.add_argument('--diseases',type=Path,default=ROOT/'var/disease_catalog_v1/build/diseases.sqlite3')
    run_parser=sub.add_parser('run');run_parser.add_argument('--frozen',type=Path,required=True);run_parser.add_argument('--url',required=True);run_parser.add_argument('--out',type=Path,required=True)
    replay_parser=sub.add_parser('refreeze');replay_parser.add_argument('--baseline',type=Path,required=True);replay_parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.mode=='freeze':freeze(args)
    elif args.mode=='refreeze':refreeze(args)
    else:run(args)
