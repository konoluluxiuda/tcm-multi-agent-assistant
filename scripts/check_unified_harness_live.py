"""One-shot synthetic API/native smoke; frozen inputs, <=7 requests, no retries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from scripts.dataops_source_manifest import manifest
from scripts.eval_research_combined import Recorder,save

ROOT=Path(__file__).resolve().parents[1]
QUESTIONS=[
    ('U01','我刚接触这个服务，想先知道能拿它交付哪些材料'),
    ('U02','Alpha和Beta各占一行，主要用途和存放环境各一列，没写的留空并标明，交一张带出处的表格'),
]


def inputs():
    return {'runtime_source_tree':manifest()['source_tree_sha256'],
            'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'fixture_sha256':hashlib.sha256((ROOT/'tests/dataops_fixture.py').read_bytes()).hexdigest(),
            'recorder_sha256':hashlib.sha256((ROOT/'scripts/eval_research_combined.py').read_bytes()).hexdigest()}


def prepare(out):
    out.mkdir(parents=True,exist_ok=False,mode=0o700)
    protocol={'version':'unified-harness-synthetic-v1','inputs':inputs(),'questions':QUESTIONS,
        'native_case':{'id':'U03','names':['Alpha','Beta','Delta'],'fields':['注意'],'target_count':2,'expected_rows':0},
        'endpoint':'https://api.deepseek.com','model':'deepseek-flash','max_requests':7,
        'goal_requests_per_case':1,'native_max_requests':5,'max_output_tokens':1200,'max_request_bytes':65536,
        'timeout_seconds':30,'retries':0,'trace_export':False,'stop_on_unknown_or_http_error':True,
        'data':'fixed synthetic questions, static schemas, synthetic aliases and tool states; optional synthetic fragments only; no real corpus, PDFs, history or key bodies',
        'claim':'integration smoke, not independent user accuracy; no change to previous experiments'}
    save(out/'protocol.json',protocol);return protocol


def execute(out):
    protocol=json.loads((out/'protocol.json').read_text())
    if protocol['inputs']!=inputs():raise ValueError('Frozen smoke inputs changed')
    save(out/'execution_started.json',{'consumed_even_if_interrupted':True})
    from dotenv import dotenv_values
    configured=dotenv_values(ROOT/'.env')
    for key in ('MODEL','API_KEY','BASE_URL'):
        value=os.getenv('RAG_'+key) or configured.get('RAG_'+key) or configured.get('CHAT_'+key)
        if not value:raise ValueError('Missing configured model')
        os.environ['RAG_'+key]=value
    from tests.dataops_fixture import make_sources
    from agents.conversation_store import ConversationStore
    from fastapi.testclient import TestClient
    from tcm.dataops.research_model import configured_model,ResearchDriver
    from tcm.dataops.research_graph import ResearchEngine,ResearchLimits
    from tcm.dataops.research_contracts import ResearchGoal
    from tcm.dataops.workspace import Workspaces
    from tcm.dataops.artifacts import verify
    records,sources=make_sources(out/'synthetic_sources')
    environment={'ENABLE_DATAOPS':'1','ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'fixed',
        'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'1','ENABLE_NATURAL_ROUTING':'1','ENABLE_INTENT_PLANNER':'0',
        'ENABLE_DATAOPS_AGENT':'0','DATAOPS_DRIVER':'disabled','RESEARCH_TOOLS_ONLY':'1',
        'DATAOPS_ROOT':str(out/'workspaces'),'RESEARCH_JOB_DB':str(out/'jobs.sqlite3'),
        'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':'','RAG_RELEASE_PATH':str(out/'synthetic_sources'),
        'TCM_DB':str(out/'synthetic_sources/herbs.sqlite3'),
        'ETCM_ENRICHMENT_DB':str(out/'synthetic_sources/enrichment.sqlite3'),
        'PHARMACOPOEIA_INDEX':str(out/'synthetic_sources/index'),'TCM_DATASET_VERSION':'fixture-v1',
        'ENABLE_LANGFUSE':'0','LANGFUSE_TRACING_ENABLED':'false','LANGCHAIN_TRACING_V2':'false','LANGSMITH_TRACING':'false',
        'ENABLE_WEB_SEARCH':'0','ENABLE_PUBMED':'0','ENABLE_IMAGE_ANALYSIS':'0','ENABLE_SPEECH':'0'}
    recorder=Recorder(out,protocol['max_requests']);stage=[''];results=[]
    def factory(*,max_requests=1):
        model=configured_model(max_requests=max_requests);recorder.attach(model,stage[0]);return model
    def checked_artifact(store,doc):
        path=store.root/doc['id']/doc['artifact']['filename']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==doc['artifact']['sha256']
        return verify(path,doc['result'])
    with patch.dict(os.environ,environment):
        import app
        memory=ConversationStore(out/'memory.sqlite3')
        with patch.object(app.agent_runtime,'memory',memory),patch('tcm.dataops.jobs.research_sources',sources), \
             patch('tcm.dataops.api.configured_sources',sources), \
             patch('tcm.dataops.research_model.configured_model',side_effect=factory),TestClient(app.app) as client:
            client.get('/conversation')
            for identifier,question in protocol['questions']:
                if recorder.stopped:
                    results.append({'id':identifier,'status':'not_run_after_provider_error'});continue
                stage[0]=identifier;started=time.monotonic()
                try:
                    body={'query':question,'request_key':'smoke-'+identifier}
                    response=client.post('/chat',json=body);save(out/(identifier+'-api.private.json'),response.json())
                    response.raise_for_status();value=response.json()
                    if identifier=='U01':
                        assert value['routing']['reason']=='unified_request_fallback' and value['model_calls']==1
                        assert value['assistant']['topic']=='capabilities'
                    else:
                        workspace=value['assistant']['workspace_id']
                        for _ in range(300):
                            doc=client.get('/api/dataops/agent-runs/'+workspace).json()['workspace']
                            if doc['status'] not in ('building','queued','running'):break
                            time.sleep(.05)
                        save(out/(identifier+'-result.private.json'),doc)
                        assert doc['status']=='complete' and len(doc['result']['rows'])==2
                        assert set(doc['goal']['document_fields'])=={'功能与主治','贮藏'}
                        assert doc['goal_understanding']['provider_requests']==1 and doc['execution']['provider_requests']==0
                        checked_artifact(Workspaces(out/'workspaces'),doc)
                    count=len(recorder.entries);again=client.post('/chat',json=body);again.raise_for_status()
                    assert len(recorder.entries)==count
                    results.append({'id':identifier,'status':'passed','duration_seconds':round(time.monotonic()-started,3),
                                    'replay_added_requests':0})
                except Exception as exc:
                    results.append({'id':identifier,'status':'failed','error_type':type(exc).__name__})
                recorder.check_stop()
            if not recorder.stopped:
                stage[0]='U03';model=factory(max_requests=protocol['native_max_requests'])
                try:
                    store=Workspaces(out/'native_workspaces');case=protocol['native_case']
                    goal=ResearchGoal(query={'names':case['names'],'limit':20},attributes=['medicinal_part'],
                                      document_fields=case['fields'],research={'target_count':case['target_count']})
                    with sources() as s:doc,_=store.reserve_agent('synthetic-smoke','native-task',goal.model_dump(),s.revision,question='三条均缺注意，只交付字段齐全的两条，不足如实交付')
                    limits=ResearchLimits();limits.model_calls=protocol['native_max_requests']
                    result=ResearchEngine(store,'synthetic-smoke',doc['id'],sources,ResearchDriver(model,authorized=True),limits=limits).run()
                    save(out/'U03-result.private.json',result)
                    assert result['status']=='partial' and result.get('artifact') and result['result']['rows']==[]
                    receipt=checked_artifact(store,result)
                    results.append({'id':'U03','status':'passed','rows':0,'verification':receipt,
                                    'decision_steps':result['progress']['model_calls']})
                except Exception as exc:results.append({'id':'U03','status':'failed','error_type':type(exc).__name__})
                finally:model.http_client.close();recorder.check_stop()
            else:results.append({'id':'U03','status':'not_run_after_provider_error'})
    report={'version':protocol['version'],'status':'passed' if all(r['status']=='passed' for r in results) else 'failed',
        'results':results,'actual_requests':len(recorder.entries),'tokens':sum(r.get('tokens',0) for r in recorder.entries),
        'requests':recorder.entries,'provider_stopped':recorder.stopped,'real_corpus_sent':False,
        'independent_accuracy':False,'old_scores_changed':False,'runtime_defaults_changed_by_smoke':False}
    save(out/'report.json',report);return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    report=execute(args.out.resolve()) if args.execute else prepare(args.out.resolve())
    print(json.dumps({k:report[k] for k in ('version','status','actual_requests','tokens','max_requests') if k in report}))
