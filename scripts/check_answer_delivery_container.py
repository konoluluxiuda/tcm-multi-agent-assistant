"""Check saved-query delivery/replay in an isolated container, models OFF.

Seeds one owner-bound proposal via local SQLite, NOT an understanding evaluation.
Only restarts a tcm-requirements-clean-* container with the specified state mount.
"""
import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4
import httpx
from agents.conversation_store import ConversationStore
from tcm.request_journal import RequestJournal
from tcm.dataops.data_access import Sources
from tcm.runtime_status import fingerprint,ROOT
from scripts.check_clean_reproduction import wait_ready
from scripts.eval_requirements_generalization import _save


def check(base,state,release,container,out):
    state=Path(state).resolve();out=Path(out);out.mkdir(parents=True,exist_ok=False,mode=0o700)
    if not container.startswith('tcm-requirements-clean-'):raise ValueError('Only dedicated acceptance containers may restart')
    info=json.loads(subprocess.check_output(['docker','inspect',container],text=True))[0]
    if not any(m['Destination']=='/app-memory' and Path(m['Source']).resolve()==state for m in info['Mounts']):raise ValueError('Unexpected state mount')
    if 'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL=0' not in info['Config']['Env']:raise ValueError('Container must disable models')
    port=info['HostConfig']['PortBindings']['8000/tcp'][0]
    if base.rstrip('/')!=f"http://127.0.0.1:{port['HostPort']}":raise ValueError('Wrong endpoint for isolated container')
    with httpx.Client(base_url=base,timeout=30) as client:
        wait_ready(client);client.get('/conversation').raise_for_status();client.get('/dataops/research').raise_for_status()
        runtime=client.get('/api/runtime').json()
        if runtime['source_revision']!=fingerprint(ROOT) or runtime['restart_required']:raise ValueError('Image/application code mismatch')
        cookie=client.cookies.get('research_selection_container')
        memory=ConversationStore(state/'conversations.sqlite3');owner=memory.key(cookie)
        if not memory.valid_cookie(cookie):raise ValueError('Cookie does not belong to mounted state')
        key='container-'+uuid4().hex
        env=dict(v.split('=',1) for v in info['Config']['Env'] if '=' in v)
        # Answer API binds the base Sources contract, not ReleaseSources' extra rules.
        with Sources(env['TCM_DB'],env['ETCM_ENRICHMENT_DB'],env['PHARMACOPOEIA_INDEX'],env.get('TCM_DATASET_VERSION')) as sources:
            revision=sources.revision
        proposal={'status':'query','question':'白术的药用部位和功能与主治','source_revision':revision,'external_model_calls':0,
            'evaluation_origin':'offline_seed_not_model_understanding','routes':[
                {'tool':'herb','request':{'name':'白术','field':'medicinal_part','resolve_unique_name':True}},
                {'tool':'research','request':{'question':'白术的功能与主治'}}]}
        journal=RequestJournal(state/'dataops/question_proposals.sqlite3')
        journal.reserve(owner,key,'offline-container-query');journal.complete(owner,key,proposal)
        response=client.post('/api/dataops/answer',json={'request_key':key})
        _save(out/'initial-response.private.json',{'http_status':response.status_code,'body':response.json()})
        response.raise_for_status();answer=response.json()
        _save(out/'answer.private.json',answer)
        if answer['status']!='success' or len(answer['answer_steps'])!=2 or answer['tool_calls_this_submission']!=2:raise ValueError('Cross-source delivery incomplete')
        if answer['model_calls']!=0:raise ValueError('Unexpected model request')
        if not answer['answer_steps'][0]['result']['records']:raise ValueError('No ETCM records')
        if not answer['answer_steps'][1]['result']['tool_result']['hits']:raise ValueError('No pharmacopoeia evidence')
        expected=answer['answer_steps']
        replay=client.post('/api/dataops/answer',json={'request_key':key}).json()
        assert replay['replayed'] and replay['tool_calls_this_submission']==0 and replay['answer_steps']==expected
        assert client.post('/api/dataops/answer',json={'request_key':key,'routes':[]}).status_code==422
        with httpx.Client(base_url=base,timeout=30) as other:
            other.get('/conversation');other.get('/dataops/research')
            assert other.get('/api/dataops/answers/'+key).status_code==404
        badkey='stale-'+uuid4().hex
        journal.reserve(owner,badkey,'offline-stale-query');journal.complete(owner,badkey,{**proposal,'source_revision':{'changed':True}})
        assert client.post('/api/dataops/answer',json={'request_key':badkey}).status_code==409
        subprocess.run(['docker','restart','--time','30',container],check=True,capture_output=True)
        wait_ready(client)
        after=client.get('/api/runtime').json()
        assert after['source_revision']==runtime['source_revision'] and after['started_at']!=runtime['started_at']
        historical=client.get('/api/dataops/answers/'+key);historical.raise_for_status()
        assert historical.json()['answer_steps']==expected
        conversation=client.get('/conversation').json()
        assert conversation['answer_delivery']['answer_steps']==expected
        replay=client.post('/api/dataops/answer',json={'request_key':key}).json()
        assert replay['replayed'] and replay['tool_calls_this_submission']==0 and replay['model_calls']==0
        _save(out/'replay.private.json',replay)
        report={'status':'passed','real_model_requests':0,'model_understanding_evaluated':False,
            'proposal_origin':'local owner-bound SQLite seed; existing API performs real tools',
            'checks':['source_fingerprint','cross_source_two_steps','replay_zero_new_tools','route_injection_rejected',
                'foreign_owner_denied','stale_proposal_rejected','container_restart','historical_read','conversation_restore','restart_replay'],
            'source_revision':after['source_revision'],'container':container,'image_id':info['Image']}
        _save(out/'summary.json',report);return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','state','release','container','out'):p.add_argument('--'+name,required=True)
    a=p.parse_args();print(json.dumps(check(a.base,a.state,a.release,a.container,a.out),ensure_ascii=False))
