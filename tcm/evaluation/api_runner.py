"""One isolated scenario/policy process. Real ASGI, real tools, bounded provider I/O.

Synthetic mode substitutes ONLY the SDK response, never the executor or scorer.
No golden references are passed to the model fixture or observation adapter.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from tcm.evaluation.requirements_benchmark import ResultStep,CaseResult,digest
from tcm.evaluation.ledger import Ledger


def _synthetic_sources(folder):
    from tests.dataops_fixture import make_sources
    from scripts.build_mineru_retrieval import build
    import fitz
    records,sources=make_sources(folder/'sources')
    root=folder/'sources';pdf=root/'synthetic.pdf'
    book=fitz.open()
    for i in range(4):book.new_page().insert_text((72,180),f'SYNTHETIC EFFICACY {i}')
    book.save(pdf);book.close()
    # A valid PDF/index, sharing exact entry IDs with the structured fixture.
    view=json.loads((root/'view/research_view.json').read_text())
    for entry,name in zip(view['entries'],('Alpha','Beta','Gamma','Delta')):
        entry['name_zh_candidate']=entry['raw_name_zh_candidate']=name
        for field in entry['fields']:
            for evidence in field['evidence']:
                evidence['source'].update(source_document='high',source_sha256=digest(pdf))
                evidence['bbox_0_1000']=[100,100,500,300]
        for evidence in entry['title_evidence']:
            evidence['source'].update(source_document='high',source_sha256=digest(pdf))
            evidence['bbox_0_1000']=[100,100,500,300]
    (root/'view/research_view.json').write_text(json.dumps(view,ensure_ascii=False))
    (root/'view/summary.json').write_text(json.dumps({'research_view_sha256':digest(root/'view/research_view.json'),'ledger_sha256':digest(root/'view/revision_ledger.json')}))
    build(root/'view',root/'query-index')
    import sqlite3
    with sqlite3.connect(root/'enrichment.sqlite3') as con:
        for i,name in enumerate(('Alpha','Beta','Gamma','Delta')):
            for herb,entry,evidence in con.execute('SELECT herb_id,entry_id,evidence FROM name_candidates WHERE entry_id=?',('entry-'+str(i),)).fetchall():
                value=json.loads(evidence);value['name_zh']=name
                con.execute('UPDATE name_candidates SET name_zh=?,evidence=? WHERE herb_id=? AND entry_id=?',(name,json.dumps(value),herb,entry))
    from tcm.dataops.data_access import Sources
    sources=lambda:Sources(root/'herbs.sqlite3',root/'enrichment.sqlite3',root/'query-index','fixture-v1')
    return sources,{'TCM_DB':str(root/'herbs.sqlite3'),'ETCM_ENRICHMENT_DB':str(root/'enrichment.sqlite3'),
        'PHARMACOPOEIA_INDEX':str(root/'query-index'),'PHARMACOPOEIA_PDF_HIGH':str(pdf),'PHARMACOPOEIA_PDF_FULL':str(pdf),
        'ETCM_SOURCE_ROOT':str(root/'raw'),'TCM_DATASET_VERSION':'fixture-v1'}


class SyntheticModel:
    """Small authored SDK fixture. Input questions only; never reads expected results."""
    max_retries=0
    def __init__(self,ledger,case,policy,current):
        self.ledger,self.case,self.policy,self.current=ledger,case,policy,current
        self.http_client=type('Client',(),{'close':lambda self:None})()
    def bind_tools(self,tools,**kwargs):self.tools=tools;return self
    def invoke(self,messages):
        from langchain_core.messages import AIMessage
        question=self.current['question'];identifier=self.ledger.reserve(self.case,self.policy,self.current['turn'])
        names=['Alpha','Beta'] if 'Beta' in question or '增加' in question else ['Alpha']
        table='表格' in question or '增加' in question
        fields=[]
        if '药用部位' in question:fields.append({'namespace':'etcm','name':'medicinal_part'})
        if '功效' in question:fields.append({'namespace':'pharmacopoeia','name':'功能与主治'})
        if '贮藏' in question:fields.append({'namespace':'pharmacopoeia','name':'贮藏'})
        if '注意' in question:fields.append({'namespace':'pharmacopoeia','name':'注意'})
        tools={x['function']['name'] for x in self.tools}
        if 'submit_requirements_v2' in tools:
            name='submit_requirements_v2'
            args={'version':'requirements-v2','status':'ready','operation':'append_fields' if '增加' in question else 'new',
                'objects':[{'ref':f'o{i+1}','mention':n} for i,n in enumerate(names)],
                'requirements':[{'id':'r1','kind':'read_fields','object_refs':[f'o{i+1}' for i in range(len(names))],'fields':fields}],
                'delivery':({'form':'table','row_policy':'keep_all'} if table else {'form':'answer'}),'unresolved':[]}
            if '数据库' in question:args.update(objects=[],requirements=[{'id':'r1','kind':'dataset','datasets':['etcm','pharmacopoeia']}])
        elif table:
            name='propose_research_goal'
            args={'status':'ready','operation':'append_fields' if '增加' in question else 'new','mode':'audit_named','names':[] if '增加' in question else names,
                'filters':[],'target_count':None,'attributes':[f['name'] for f in fields if f['namespace']=='etcm'],
                'document_fields':[f['name'] for f in fields if f['namespace']=='pharmacopoeia'],'monograph_only':None,'issues':[],'unresolved':[]}
        else:
            name='submit_query_intent';args={'action':'dataset_info'} if '数据库' in question else {'action':'herb_field','entities':names,'fields':['medicinal_part']}
        self.ledger.received(identifier,200,{'input_tokens':10,'output_tokens':2,'total_tokens':12})
        return AIMessage(content='',tool_calls=[{'id':str(identifier),'name':name,'args':args}],usage_metadata={'input_tokens':10,'output_tokens':2,'total_tokens':12})


def _bind_real(model,ledger,case,policy,current,protocol,folder):
    from scripts.eval_requirements_generalization import _save
    pending=[]
    def before(req):
        if req.url.scheme!='https' or req.url.host!='api.deepseek.com' or req.url.path not in ('/chat/completions','/v1/chat/completions') or req.method!='POST' or len(req.content)>protocol['max_request_bytes']:raise ValueError('Provider protocol mismatch')
        payload=json.loads(req.content)
        if (payload.get('model')!=protocol['model'] or payload.get('temperature')!=protocol['temperature'] or
            payload.get('thinking')!={'type':protocol['thinking']} or payload.get('max_completion_tokens')!=protocol['max_output_tokens'] or payload.get('stream') is not False):
            raise ValueError('Wire model parameters differ from frozen protocol')
        identifier=ledger.reserve(case,policy,current['turn']);pending.append(identifier)
        # Content may contain source name candidates; private run directory only. Never log headers.
        _save(folder/f'dispatch-{identifier}.private.json',{'request_id':identifier,'turn':current['turn'],'body':json.loads(req.content)})
    def after(resp):
        resp.read()
        try:body=resp.json()
        except ValueError:body={'body_sha256':__import__('hashlib').sha256(resp.content).hexdigest()}
        ledger.received(pending[-1],resp.status_code,body.get('usage'))
        _save(folder/f'response-{pending[-1]}.private.json',{'http':resp.status_code,'body':body})
    model.http_client.event_hooks['request'].append(before);model.http_client.event_hooks['response'].append(after)
    return model


def _evidence_sources(value,obj,ns,name,source):
    """Normalize evidence attached to THIS field, never search whole response text."""
    base={'object_ref':obj,'namespace':ns,'name':name,'value':value}
    if not isinstance(source,dict):return []
    # ETCM file anchors.
    result=[]
    path=source.get('relative_file_path') or source.get('relative_path')
    if path:result.append({**base,'source_document':path,'source_sha256':source.get('file_sha256')})
    # Document index hits store original page anchors under evidence[].source.
    ev=source.get('evidence') or source.get('payload',{}).get('evidence') or []
    for item in ev:
        anchor=item.get('source',item)
        result.append({**base,'source_document':anchor.get('source_document'),
            'source_sha256':anchor.get('source_sha256'),'page':anchor.get('source_pdf_page'),
            'chunk_id':source.get('chunk_id') or source.get('id')})
    if not result and source.get('source_document'):
        result.append({**base,'source_document':source.get('source_document'),'source_sha256':source.get('source_sha256'),
            'page':source.get('source_pdf_page') or source.get('page'),'chunk_id':source.get('chunk_id')})
    return result


def observe(body,http,turn):
    status=body.get('status')
    proposal=body.get('proposal') or {};assistant=body.get('assistant') or {}
    if http>=400:status='unsupported' if http==403 else 'unavailable'
    elif status not in ('success','partial','unavailable','complete'):
        state=proposal.get('status') or assistant.get('status')
        if state in ('needs_input','clarification_needed'):status='clarification'
        elif state in ('unsupported','unsupported_delivery'):status='unsupported'
        elif any(body.get(k) for k in ('tcm','research','assistant')):status='success'
        else:status='unavailable'
    step=ResultStep(turn_id=turn,http_status=http,terminal_state=status or 'unavailable',response_text=str(body.get('response') or body.get('detail') or proposal.get('message') or ''),
                    answer_steps=body.get('answer_steps') or [],usage={'model_calls':body.get('model_calls'),'tool_calls':body.get('tool_calls_this_submission')})
    blocks=body.get('answer_steps') or [{'result_key':k+'_result','result':body[k]} for k in ('tcm','research','assistant') if isinstance(body.get(k),dict)]
    objects=[]
    for block in blocks:
        result=block.get('result') or {};route=block.get('route') or {};step.routes.append(route)
        if block.get('outcome') in ('partial','unavailable'):step.terminal_state='partial' if status!='unavailable' else status
        if result.get('status') in ('clarification_needed','needs_input','name_not_resolved'):step.terminal_state='clarification'
        for rec in result.get('records',[]):
            fields=rec.get('fields',{});obj=(fields.get('name_pinyin') or {}).get('value') or rec.get('name') or rec.get('display_name') or route.get('request',{}).get('name')
            if not obj:continue
            objects.append(obj)
            for name,cell in fields.items():
                if not isinstance(cell,dict):continue
                step.fields.append({'object_ref':obj,'namespace':'etcm','name':name,'value':cell.get('value'),'state':cell.get('state')})
                step.sources.extend(_evidence_sources(cell.get('value'),obj,'etcm',name,rec.get('source',{})))
        research=result.get('tool_result') or result
        for hit in research.get('hits',[]):
            obj=hit.get('name');name=hit.get('label') or hit.get('field');value=hit.get('text') or hit.get('raw_text')
            if not obj or not name:continue
            objects.append(obj);step.fields.append({'object_ref':obj,'namespace':'pharmacopoeia','name':name,'value':value,'state':'present'})
            step.sources.extend(_evidence_sources(value,obj,'pharmacopoeia',name,hit))
        relation=result.get('relations')
        if relation and result.get('status')=='ok' and 'data' in relation:
            names=[]
            for group in relation['groups']:
                choices=group.get('options',[])
                if len(choices)!=1:continue
                obj=choices[0]['name'];names.append(obj);objects.append(obj)
                source=choices[0]['source'];kind=relation['kind']
                # Only returned source records are observed; no claim of exhaustive retrieval.
                value=json.dumps(relation['data'].get('items',[]),ensure_ascii=False,sort_keys=True)
                anchors=_evidence_sources(value,obj,'etcm',kind,source)
                for anchor in anchors:anchor.update(total=relation['data'].get('total'),returned_count=relation['data'].get('returned_count'))
                step.sources.extend(anchors)
            if len(names)==len(relation['groups']):
                step.relations.append({'namespace':'etcm','name':relation['kind'],'object_refs':names,
                    'returned_count':relation['data'].get('returned_count'),'total':relation['data'].get('total')})
        if result.get('topic')=='dataset_info':
            step.fields.append({'object_ref':None,'namespace':'dataset','name':'dataset_info','value':step.response_text,'state':'present'})
            step.sources.append({'object_ref':None,'namespace':'dataset','name':'dataset_info','value':result.get('message',step.response_text),'source_document':'runtime_catalog'})
    step.objects=list(dict.fromkeys(objects))
    step.actual_policy=(body.get('routing') or {}).get('reason') or proposal.get('policy') or 'local_or_unknown'
    return step


def observe_table(step,doc,path):
    r=doc['result'];step.table_result=r;step.terminal_state=doc['status'];step.artifact_path=str(path);step.artifact_sha256=digest(path)
    step.actual_policy=doc.get('goal_understanding',{}).get('policy') or 'local'
    step.objects=[];step.fields=[];step.sources=[]
    document_fields=r['goal']['document_fields']
    for row in r['rows']:
        obj=row['name'];step.objects.append(obj)
        for name,cell in row['cells'].items():
            ns='pharmacopoeia' if name in document_fields else 'etcm'
            step.fields.append({'object_ref':obj,'namespace':ns,'name':name,'value':cell['value'],'state':cell['status']})
            for identifier in cell['evidence_ids']:
                source=r['evidence'][identifier]['source']
                step.sources.extend(_evidence_sources(cell['value'],obj,ns,name,source))
    step.response_text=json.dumps(r,ensure_ascii=False)
    step.usage.update(tool_calls=r.get('tool_calls'),provider_requests=doc.get('goal_understanding',{}).get('provider_requests'))


def run(out,scenario,policy):
    from scripts.eval_requirements_generalization import _save
    protocol=json.loads((out/'protocol.json').read_text());folder=out/'cases'/f'{scenario.id}_{policy}'
    ledger=Ledger(out/'ledger.sqlite3',protocol['budget']);current={};steps=[];parent=None;parent_path=None;models=[]
    base={'ENABLE_DATAOPS':'1','ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'fixed','ENABLE_DATAOPS_AGENT':'0','DATAOPS_DRIVER':'disabled',
        'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'0' if policy=='R' else '1',
        'DATAOPS_UNDERSTANDING_POLICY':'requirements_v3' if policy=='V' else 'local_then_one_model_v1',
        'DATAOPS_ROOT':str(folder/'state/dataops'),'CHAT_MEMORY_DB':str(folder/'state/memory.sqlite3'),
        'RESEARCH_JOB_DB':str(folder/'state/jobs.sqlite3'),'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':'',
        'ENABLE_NATURAL_ROUTING':'1','RESEARCH_TOOLS_ONLY':'1','ENABLE_INTENT_PLANNER':'0','ENABLE_PHARMACOPOEIA_RESEARCH':'1',
        'ENABLE_RAG_GENERATION':'0','ENABLE_RAG_WORKBENCH':'0','ENABLE_WEB_SEARCH':'0','ENABLE_PUBMED':'0',
        'ENABLE_IMAGE_ANALYSIS':'0','ENABLE_SPEECH':'0','ENABLE_LANGFUSE':'0','LANGFUSE_TRACING_ENABLED':'false',
        'LANGCHAIN_TRACING_V2':'false','LANGSMITH_TRACING':'false','CHAT_COOKIE_SECURE':'0'}
    if protocol['mode']=='synthetic':
        sources,data=_synthetic_sources(folder)
        base.update(RAG_MODEL='fixture',RAG_BASE_URL='https://api.deepseek.com',RAG_API_KEY='test-only')
    else:
        from tcm.dataops.research_sources import ReleaseSources
        release=Path(protocol['release']);sources=lambda:ReleaseSources(release)
        config=json.loads((release/'sources.private.json').read_text())['config']
        data={'RAG_RELEASE_PATH':str(release),'PHARMACOPOEIA_INDEX':str(release/'index'),
            **{k:config[v] for k,v in [('TCM_DB','herbs'),('ETCM_ENRICHMENT_DB','enrichment'),('ETCM_TARGET_DB','targets'),('ETCM_SOURCE_ROOT','source_root'),('PHARMACOPOEIA_PDF_HIGH','pdf_high'),('PHARMACOPOEIA_PDF_FULL','pdf_full')]}}
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ,{**base,**data}));stack.enter_context(patch('dotenv.load_dotenv',return_value=False))
        if protocol['mode']=='synthetic':
            import socket
            stack.enter_context(patch.object(socket.socket,'connect',side_effect=RuntimeError('synthetic mode forbids network')))
            stack.enter_context(patch.object(socket,'getaddrinfo',side_effect=RuntimeError('synthetic mode forbids DNS')))
        import app
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from tcm.dataops.research_model import configured_model
        def factory(*,max_requests=1):
            if policy=='R':raise AssertionError('Local baseline attempted a model call')
            model=(SyntheticModel(ledger,scenario.id,policy,current) if protocol['mode']=='synthetic' else
                _bind_real(configured_model(max_requests=max_requests),ledger,scenario.id,policy,current,protocol,folder))
            models.append(model);return model
        stack.enter_context(patch.object(app.agent_runtime,'memory',ConversationStore(folder/'state/memory.sqlite3')))
        stack.enter_context(patch('tcm.dataops.api.configured_sources',sources));stack.enter_context(patch('tcm.dataops.jobs.research_sources',sources))
        stack.enter_context(patch('tcm.dataops.research_model.configured_model',side_effect=factory))
        client=stack.enter_context(TestClient(app.app));client.get('/conversation');client.get('/dataops/research')
        for turn in scenario.turns:
            current.update(turn=turn.id,question=turn.question);started=time.monotonic();key=f'eval-{scenario.id}-{policy}-{turn.id}'
            oldhash=digest(parent_path) if parent_path else None
            if scenario.entrypoint=='chat':response=client.post('/chat',json={'query':turn.question,'request_key':key})
            else:
                payload={'question':turn.question,'request_key':key,'use_model':policy!='R'}
                if parent:payload.update(parent_id=parent['id'],expected_revision=parent['revision'])
                response=client.post('/api/dataops/research-questions',json=payload)
                _save(folder/f'{turn.id}-proposal.private.json',response.json())
                if response.json().get('proposal',{}).get('status')=='query':response=client.post('/api/dataops/answer',json={'request_key':key})
            body=response.json();_save(folder/f'{turn.id}-response.private.json',body)
            step=observe(body,response.status_code,turn.id);step.request_id=response.headers.get('X-Request-ID')
            wid=body.get('workspace',{}).get('id') or body.get('assistant',{}).get('workspace_id')
            if wid:
                deadline=time.monotonic()+90
                while True:
                    reply=client.get('/api/dataops/agent-runs/'+wid);reply.raise_for_status();doc=reply.json()['workspace']
                    if doc['status'] not in ('building','queued','running'):break
                    if time.monotonic()>=deadline:raise TimeoutError('fixed task did not finish')
                    time.sleep(.02)
                _save(folder/f'{turn.id}-workspace.private.json',doc)
                if doc.get('artifact'):
                    response_file=client.get('/api/dataops/workspaces/'+wid+'/artifact');response_file.raise_for_status()
                    path=folder/f'{turn.id}.xlsx'
                    with path.open('xb') as stream:stream.write(response_file.content)
                    observe_table(step,doc,path)
                    if parent_path:
                        # Re-download the PARENT endpoint, rather than just hashing a local copy twice.
                        old_response=client.get('/api/dataops/workspaces/'+parent['id']+'/artifact');old_response.raise_for_status()
                        step.parent_artifact_path=str(parent_path);step.parent_artifact_before=oldhash
                        step.parent_artifact_after=__import__('hashlib').sha256(old_response.content).hexdigest()
                    parent=doc;parent_path=path
                else:step.terminal_state='failed'
            if step.request_id:
                diag=client.get('/api/dataops/diagnostics/'+step.request_id)
                _save(folder/f'{turn.id}-diagnostic.private.json',{'http':diag.status_code,'body':diag.json()})
            step.duration_seconds=time.monotonic()-started;steps.append(step)
            _save(folder/f'{turn.id}-observation.private.json',step.model_dump())
            if ledger.stopped() or step.terminal_state not in ('success','complete'):break
        for model in models:model.http_client.close()
    return CaseResult(scenario_id=scenario.id,policy=policy,steps=steps,status='completed' if len(steps)==len(scenario.turns) else 'failed')


def main():
    from scripts.eval_requirements_generalization import _read_suite,_save
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--case',required=True);p.add_argument('--policy',choices=('R','G','V'),required=True)
    a=p.parse_args();s=next(s for s in _read_suite(a.out/'questions.private.json') if s.id==a.case)
    r=run(a.out,s,a.policy);_save(a.out/'cases'/f'{s.id}_{a.policy}'/'result.private.json',r.model_dump())

if __name__=='__main__':main()
