"""Rehearse existing local demos; no live model calls or new benchmark claims."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]

DATAOPS_EXAMPLES={
    'names':['白术','黄芪'],
    'question':'整理白术和黄芪的功效、注意事项，保留出处并生成表格',
    'selection_names':['白术','黄芪','川芎','甘草','大黄'],
    'selection_question':'从白术、黄芪、川芎、甘草、大黄中挑两条功效和注意字段齐全的，保留出处并生成表格',
    'selection_fields':['功能与主治','注意'],'selection_count':2,
    'ambiguous_question':'白术的来源做成表',
    'unsupported_question':'整理白术的功效，并联网补齐注意，生成表格',
}


def local_url(value):
    url=urlsplit(value)
    return (url.scheme=='http' and url.hostname=='127.0.0.1' and not url.username and not url.password and
            not url.query and not url.fragment and url.path in ('','/'))


def rehearse_dataops(url,out,*,client=None,examples=None,poll_timeout=90):
    """Use the public API and verify downloaded files, without a model request.

    Creates three private workspaces in an isolated cookie session. The client
    repository revision and server runtime fingerprint are recorded separately;
    a container fingerprint is not assumed equal to its host checkout.
    """
    import httpx
    from scripts.dataops_source_manifest import manifest
    from tcm.dataops.artifacts import verify
    if not local_url(url):raise ValueError('Only a 127.0.0.1 HTTP preview root is supported')
    out=Path(out);out.mkdir(parents=True,exist_ok=False,mode=0o700)
    cases=examples or DATAOPS_EXAMPLES;before=manifest();owned=client is None
    client=client or httpx.Client(base_url=url.rstrip('/'),timeout=30,follow_redirects=False)
    report={'status':'failed','mode':'dataops_fixed_api','phase':'runtime','stages':[],
            'external_model_calls':0,'model_calls_verified':False,'source_quality':'candidate extraction, pending human signoff',
            'business_accuracy':'not_measured','new_live_benchmark':False,
            'client_git_commit':before['git_commit'],'client_runtime_source_tree':before['source_tree_sha256'],
            'client_revision_does_not_identify_server_revision':True}
    script_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();report['client_script_sha256']=script_sha
    class RehearsalCheckFailed(ValueError):pass
    def save(name,value):
        with (out/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
        (out/name).chmod(0o600)
    def require(condition,kind):
        if not condition:raise RehearsalCheckFailed(kind)
    def get(path):
        response=client.get(path);response.raise_for_status();return response
    def submit(label,question,parent=None):
        body={'question':question,'request_key':uuid4().hex,'use_model':False}
        if parent:body.update(parent_id=parent['id'],expected_revision=parent['revision'])
        save(label+'-request.private.json',body)
        response=client.post('/api/dataops/research-questions',json=body);response.raise_for_status()
        value=response.json();save(label+'-accepted.private.json',value);return value
    def finish(label,value):
        require(bool(value.get('workspace')),'task_not_created')
        identifier=value['workspace']['id'];deadline=time.monotonic()+poll_timeout
        while True:
            snapshot=get('/api/dataops/agent-runs/'+identifier).json()
            if snapshot['job']['status'] not in ('queued','running'):break
            require(time.monotonic()<deadline,'task_poll_timeout');time.sleep(.3)
        doc=snapshot['workspace'];save(label+'.private.json',doc)
        require(doc['status']=='complete' and bool(doc.get('artifact')),'task_not_complete')
        calls=doc['goal_understanding']['provider_requests']+doc['result']['research']['provider_requests']
        report['external_model_calls']+=calls
        require(calls==0,'unexpected_model_request')
        require(doc['progress']['driver']=='research_fixed_field_policy','unexpected_execution_driver')
        response=get('/api/dataops/workspaces/'+identifier+'/artifact');data=response.content
        require(hashlib.sha256(data).hexdigest()==doc['artifact']['sha256'],'artifact_hash_mismatch')
        target=out/(label+'.xlsx');target.write_bytes(data);target.chmod(0o600)
        checked=verify(target,doc['result']);require(checked['status']=='passed','artifact_verification_failed')
        report['stages'].append({'stage':label,'workspace_id':identifier,'revision':doc['revision'],
            'rows':len(doc['result']['rows']),'missing_cells':doc['result']['coverage']['missing_cells'],
            'artifact_sha256':doc['artifact']['sha256'],'artifact':target.name,'verification':checked})
        return doc
    try:
        get('/dataops/research')  # Obtain a separate owner cookie; never reuse user history.
        runtime=get('/api/runtime').json();report['server_runtime']=runtime
        require(runtime['restart_required'] is False and runtime['source_revision']==runtime['disk_revision'],'stale_server_restart_required')
        caps=get('/api/dataops/capabilities').json()['research_selection']
        require(caps['enabled'] and caps['driver']=='fixed' and not caps['execution_model_enabled'],'fixed_execution_required')
        original={w['id'] for w in get('/api/dataops/workspaces').json()['items']}
        report['phase']='named_audit'
        first=finish('v1',submit('v1',cases['question']))
        require(first['goal']['research']['mode']=='audit_named' and set(first['goal']['query']['names'])==set(cases['names']),'named_scope_mismatch')
        require(len(first['result']['rows'])==len(cases['names']) and set(first['goal']['document_fields'])=={'功能与主治','注意'},'named_fields_mismatch')
        report['phase']='append_revision'
        second=finish('v2',submit('v2','增加贮藏，保留旧字段',first))
        require(second['parent_id']==first['id'] and second['revision']==first['revision']+1 and second['goal']['query']==first['goal']['query'],'parent_scope_mismatch')
        require(second['goal']['document_fields']==first['goal']['document_fields']+['贮藏'],'revision_fields_mismatch')
        old_rows={r['source_id']:r for r in first['result']['rows']};new_rows={r['source_id']:r for r in second['result']['rows']}
        require(set(old_rows)==set(new_rows),'revision_objects_changed')
        for sid,row in old_rows.items():
            require(all(new_rows[sid]['cells'][f]==cell for f,cell in row['cells'].items()),'old_cells_changed')
        old=get('/api/dataops/workspaces/'+first['id']+'/artifact').content
        require(hashlib.sha256(old).hexdigest()==first['artifact']['sha256'],'old_artifact_changed')
        report['phase']='candidate_selection'
        selected=finish('selection',submit('selection',cases['selection_question']))
        require(selected['goal']['research']['mode']=='select_complete' and selected['goal']['research']['target_count']==cases['selection_count'],'selection_goal_mismatch')
        require(set(selected['goal']['query']['names'])==set(cases['selection_names']) and set(selected['goal']['document_fields'])==set(cases['selection_fields']),'selection_scope_mismatch')
        require(len(selected['result']['rows'])==cases['selection_count'] and selected['result']['research']['qualified_count']==cases['selection_count'],'selection_incomplete')
        report['phase']='clarification_controls'
        for label,q in [('ambiguous',cases['ambiguous_question']),('unsupported',cases['unsupported_question'])]:
            value=submit(label,q)
            require('workspace' not in value and value['proposal']['status']=='needs_input' and value['proposal']['goal'] is None and value['proposal']['external_model_calls']==0,'unsafe_control_accepted')
            report['stages'].append({'stage':label,'status':'needs_input','task_created':False})
        final={w['id'] for w in get('/api/dataops/workspaces').json()['items']}
        require(final-original=={w['id'] for w in (first,second,selected)},'unexpected_workspaces_created')
        require(before['files']==manifest()['files'] and script_sha==hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'client_sources_changed')
        report.update(status='passed',phase='complete',old_file_unchanged=True,old_cells_unchanged=True,
                      clarification_creates_no_task=True,client_sources_unchanged=True,model_calls_verified=True)
    except Exception as exc:
        report.update(error_kind=type(exc).__name__)
        # Only our own fixed categories are copied, never server error text/headers.
        if isinstance(exc,RehearsalCheckFailed):report['reason']=str(exc)
    finally:
        if owned:client.close()
        save('summary.json',report)
    return report


def fingerprints():
    paths=[ROOT/'app.py',ROOT/'templates/index.html']
    for directory,pattern in [('tcm','*.py'),('tcm','*.js'),('agents','*.py'),('tests','test_research_loop_api.py'),('tests','test_pair_checkpoint.py'),('tests','test_runtime_backup.py')]:
        paths.extend((ROOT/directory).glob(pattern))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--dataops',action='store_true',help='Rehearse current fixed DataOps API and files; no browser or external model')
    p.add_argument('--url',help='Optional loopback preview with --synthetic-loop enabled')
    p.add_argument('--browser-python',type=Path);p.add_argument('--browser',type=Path)
    args=p.parse_args()
    if args.dataops:
        if not args.url or not local_url(args.url):p.error('--dataops requires a 127.0.0.1 HTTP root URL')
        result=rehearse_dataops(args.url,args.out.resolve())
        print(json.dumps({'status':result['status'],'out':str(args.out),'model_calls':result['external_model_calls'] if result['model_calls_verified'] else None,'phase':result['phase']}))
        return 0 if result['status']=='passed' else 1
    if args.url:
        url=urlsplit(args.url)
        if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1') or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            p.error('--url must be a local HTTP preview root')
        if not args.browser_python or not args.browser:p.error('Browser runtime and executable required with --url')
    args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    before=fingerprints();(args.out/'source_manifest.json').write_text(json.dumps(before,indent=2)+'\n')
    stages=[('contracts',[sys.executable,'-m','unittest','tests.test_research_loop_api','tests.test_pair_checkpoint','tests.test_runtime_backup','-q']),
            ('loop_crashes',[sys.executable,'-m','scripts.check_research_loop_recovery','--out',str(args.out/'loop_crashes')])]
    if args.url:
        base=args.url.rstrip('/')+'/'
        stages.extend([
            ('real_pair_browser',[str(args.browser_python),'-m','scripts.check_pair_research_browser','--url',base,'--browser',str(args.browser),'--out',str(args.out/'real_pair')]),
            ('synthetic_loop_browser',[str(args.browser_python),'-m','scripts.check_research_loop_browser','--url',base,'--browser',str(args.browser),'--out',str(args.out/'loop_browser.json')])])
    results=[]
    for name,command in stages:
        with (args.out/(name+'.log')).open('w') as log:
            child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,start_new_session=True)
            try:code=child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait();code=124
        results.append({'stage':name,'exit_code':code,'passed':code==0,'log':name+'.log'})
        print(json.dumps(results[-1]),flush=True)
        if code:break
    stable=before==fingerprints()
    report={'status':'passed' if stable and len(results)==len(stages) and all(r['passed'] for r in results) else 'failed',
            'stages':results,'source_files_unchanged':stable,'browser_requested':bool(args.url),
            'live_model_calls':0,'business_accuracy':'not_measured','latency_and_cost_benchmark':'not_measured',
            'limits':['Known development scenarios; not independent users','Historical audit metrics are not recomputed',
                      'Does not verify that an existing preview process loaded the current files',
                      'No new PostgreSQL SIGKILL or full database restore drill in this rehearsal']}
    (args.out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'out':str(args.out)}));return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
