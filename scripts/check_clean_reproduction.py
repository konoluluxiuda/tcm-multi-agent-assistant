"""New container + exclusive empty state, then recreate and verify durable delivery.

Uses registered read-only data; does not rebuild the corpus or test active-job crashes.
No model credentials, automatic request retries, or replacement of existing containers.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx

from scripts.eval_user_chat_replay import check_task, save
from scripts.rehearse_recruitment_demo import rehearse_dataops
from scripts.run_research_selection_container import start


def wait_ready(client, timeout=90):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            response=client.get('/ready/research')
            if response.status_code==200 and response.json().get('status')=='ready':return
        except httpx.TransportError:pass
        time.sleep(.3)
    raise ValueError('Clean service startup timed out')


def verify_recovery(client, out, before, *, timeout=90):
    """Fail on lost session/history, stale code or changed bytes before creating v3."""
    runtime=client.get('/api/runtime').json()
    if runtime['restart_required'] or runtime['source_revision']!=before['runtime']['source_revision']:
        raise ValueError('Source changed during container recreation')
    if runtime['started_at']==before['runtime']['started_at']:raise ValueError('Process was not recreated')
    conversation=client.get('/conversation');conversation.raise_for_status()
    if conversation.json()!=before['conversation']:raise ValueError('Conversation was not restored')
    items=client.get('/api/dataops/workspaces');items.raise_for_status()
    docs={w['id']:w for w in items.json()['items']}
    if set(docs)!=set(before['artifacts']):raise ValueError('Workspace ownership/list was not restored')
    for identifier,sha in before['artifacts'].items():
        response=client.get('/api/dataops/workspaces/'+identifier+'/artifact');response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest()!=sha:raise ValueError('Recovered artifact bytes changed')
    parent=before['parent']
    body={'question':'增加用法与用量，保留旧字段','request_key':uuid4().hex,'use_model':False,
          'parent_id':parent['id'],'expected_revision':parent['revision']}
    response=client.post('/api/dataops/research-questions',json=body);response.raise_for_status()
    value=response.json();save(out/'v3-accepted.private.json',value)
    doc=value['workspace'];save(out/'v3-request.private.json',body)
    _,receipt=check_task(client,{'assistant':{'workspace_id':doc['id']}},
        {'mode':'audit_named','fields':['功能与主治','注意','贮藏','用法与用量'],
         'rows':['BaiZhu','HuangQi'],'preserve_parent':True},out,
        (parent,Path(before['parent_artifact'])),timeout=timeout)
    final=client.get('/api/dataops/agent-runs/'+doc['id']).json()['workspace']
    if final['goal_understanding']['provider_requests'] or final['result']['research']['provider_requests']:
        raise ValueError('Recovery unexpectedly invoked a model')
    return {'status':'passed','conversation_restored':True,'workspace_count_restored':len(docs),
            'old_artifacts_unchanged':len(docs),'v3_after_recreation':receipt,'v3_revision':final['revision'],
            'source_revision':runtime['source_revision'],'new_process_verified':True,'external_model_calls':0}


def run(image, release, out, port, name):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False,mode=0o700)
    state=out/'empty-state'
    if state.exists():raise ValueError('State must not exist before clean startup')
    save(out/'protocol.json',{'image':image,'release':str(Path(release).resolve()),'state_initially_absent':True,
        'model_enabled':False,'reproduction_scope':'new dependency image + empty state; existing read-only registered corpus',
        'recovery_scope':'completed workspaces, chat cookie/history, artifacts and new child revision after container recreation; not in-flight crash recovery'})
    report={'status':'failed','stage':'startup'}
    client=httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=30,follow_redirects=False)
    try:
        save(out/'startup.private.json',start(image,Path(release),state,port,name));wait_ready(client)
        packages=subprocess.check_output(['docker','exec',name,'python','-m','pip','check'],text=True)
        save(out/'dependencies.json',{'pip_check':packages.strip(),'image_id':subprocess.check_output(
             ['docker','inspect','--format={{.Image}}',name],text=True).strip()})
        report['stage']='query_and_delivery'
        client.get('/dataops/research').raise_for_status()
        if client.get('/api/dataops/workspaces').json()['items']:raise ValueError('Initial state was not empty')
        reply=client.post('/chat',json={'query':'查看白术的药用部位和科名','request_key':uuid4().hex})
        reply.raise_for_status();value=reply.json();save(out/'query.private.json',value)
        for key in ('family','medicinal_part'):
            if value['tcm']['records'][0]['fields'][key]['state']!='present':raise ValueError('Documented field query failed')
        demo=rehearse_dataops(str(client.base_url),out/'delivery',client=client)
        if demo['status']!='passed':raise ValueError('Documented delivery rehearsal failed: '+str(demo.get('reason')))
        parent=json.loads((out/'delivery/v2.private.json').read_text())
        before={'runtime':client.get('/api/runtime').json(),'conversation':client.get('/conversation').json(),
                'artifacts':{r['workspace_id']:r['artifact_sha256'] for r in demo['stages'] if r.get('artifact')},
                'parent':parent,'parent_artifact':str(out/'delivery/v2.xlsx')}
        save(out/'before-recreation.private.json',before)
        report['stage']='recreate_and_recover'
        subprocess.run(['docker','stop','--time','30',name],check=True,capture_output=True)
        # Keep the stopped container for rollback/debugging; never delete state.
        subprocess.run(['docker','rename',name,name+'-before-recreation'],check=True,capture_output=True)
        save(out/'recreated.private.json',start(image,Path(release),state,port,name));wait_ready(client)
        recovery=verify_recovery(client,out,before)
        report.update(status='passed',stage='complete',delivery=demo['stages'],recovery=recovery,
                      container=name,port=port,initial_state_empty=True,external_model_calls=0)
    except Exception as exc:
        report.update(error_kind=type(exc).__name__,reason=str(exc)[:180])
    finally:
        client.close();save(out/'summary.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True);parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--release',type=Path,default=Path('var/data_releases/r1_20261030'))
    parser.add_argument('--port',type=int,default=7916);parser.add_argument('--name',default='tcm-clean-reproduction-v1')
    args=parser.parse_args();result=run(args.image,args.release,args.out,args.port,args.name)
    print(json.dumps({k:result[k] for k in ('status','stage','reason','port') if k in result},ensure_ascii=False))
    raise SystemExit(0 if result['status']=='passed' else 1)
