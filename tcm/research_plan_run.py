"""Compile question-only plans into the existing local A loop and audit store."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tcm.research_plan_intent import AnalysisPlan, validate_question, local_plan
from tcm.research_run_scope import RunScope
from tcm.research_run_local import run_local_actions
from tcm.research_run_store import RunStore
from tcm.target_similarity import file_token, SnapshotChanged
from tcm.research_evidence_store import for_run, preview, canonical
from tcm.research_business_tools import REPORT_TOOLS,SPECS
from tcm.research_jobs import JobConflict,JobBusy

KIND='analysis_plan_focus_v1'


def signature(plan):
    tools={s.call.tool for s in plan.steps}
    keys=set()
    if tools & {'search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set'}:keys.add('ETCM_DISEASE_DB')
    if tools & {'herb_target_profile','herb_target_similarity','rank_herbs_for_gene_set','herb_attributes','herb_relation_records','pair_evidence_report'}:keys.update(('TCM_DB','ETCM_ENRICHMENT_DB'))
    values={key:[str(Path(os.environ[key]).resolve()),file_token(os.environ[key])] for key in sorted(keys)}
    if tools & {'pharmacopoeia_lookup','pair_evidence_report'}:
        index=Path(os.environ['PHARMACOPOEIA_INDEX']);values['pharmacopoeia']=[str(index.resolve()),file_token(index/'research.sqlite3'),file_token(index/'manifest.json')]
    return hashlib.sha256(json.dumps([values,os.getenv('TCM_DATASET_VERSION')],sort_keys=True).encode()).hexdigest()


def response(status,message,**extra):
    return {'topic':'research_plan','status':status,'message':message,
            'llm_calls':0,'network_calls':0,**extra}


def pending(plan,question,selections,stamp,step,options,selection_key=None):
    # Only bounded source IDs/labels and the original question, never target rows.
    options=options[:20]
    focus={'kind':KIND,'question':question,'plan':plan.model_dump(),'selections':selections,
           'signature':stamp,'pending_step':step,'allowed_sources':[o['id'] for o in options]}
    focus['selection_key']=selection_key or str(step)
    choices=[{'label':o['label'],'source':o['source'],
              'query':f"/analysis-plan select {step} {o['id']}"} for o in options]
    focus['options']=choices
    return response('clarification_needed','找到多个或非精确匹配的来源，请选择本次要分析的一条；原问题会保留。',options=choices),focus


def compile_scope(plan,selections):
    from tcm.chat import execute_turn
    requirements=[];herbs={};searches={};selected_diseases={};selected_herbs={}
    for i,step in enumerate(plan.steps):
        if step.call.tool=='pair_evidence_report':
            for j,name in enumerate(step.call.arguments.herbs):
                if f'{i}:{j}' in selections:selected_herbs[name]=selections[f'{i}:{j}']
            continue
        name=step.call.arguments.name
        if step.call.tool in ('herb_target_profile','herb_target_similarity','herb_attributes','herb_relation_records') and str(i) in selections:
            selected_herbs[name]=selections[str(i)]
        if step.call.tool in ('search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set') and str(i) in selections:
            selected_diseases[name]=selections[str(i)]
        if step.call.tool=='search_disease_sources':searches[name]='step-'+str(i)
    def resolve_herb(name):
        if name in herbs:return herbs[name],None
        selected=selected_herbs.get(name)
        request={'source_record_id':selected or name} if selected or name.startswith('etcm2-local:') else {'name':name,'resolve_unique_name':True}
        found,_=execute_turn({**request,'profile':True},None)
        if found['status']!='ok':
            return None,[{'id':r['source_record_id'],'label':r['fields']['name_pinyin']['value'],'source':r['source']} for r in found.get('records',[])]
        herbs[name]=found['records'][0]['source_record_id'];return herbs[name],None
    for i,step in enumerate(plan.steps):
        call=step.call.model_dump();derived=None
        if step.call.tool=='pair_evidence_report':
            ids=[]
            for j,name in enumerate(step.call.arguments.herbs):
                sid,options=resolve_herb(name)
                if options is not None:return None,(i,options,f'{i}:{j}')
                ids.append(sid)
            if len(set(ids))!=2:return None,(i,[],'same_source')
            call['arguments']['source_ids']=ids
        else:
            name=call['arguments']['name']
            if step.call.tool in ('herb_target_profile','herb_target_similarity','herb_attributes','herb_relation_records'):
                sid,options=resolve_herb(name)
                if options is not None:return None,(i,options,str(i))
                call['arguments']['name']=sid
            elif step.call.tool in ('disease_gene_profile','rank_herbs_for_gene_set') and not name.startswith('etcm-disease:'):
                if name in selected_diseases:call['arguments']['name']=selected_diseases[name]
                else:
                    if name not in searches:
                        identifier='discovery-'+str(i);searches[name]=identifier
                        requirements.append({'id':identifier,'call':{'tool':'search_disease_sources','arguments':{'name':name}}})
                    derived=searches[name];call['arguments'].pop('name')
        requirements.append({'id':'step-'+str(i),'call':call,'source_from':derived,'all_pages':step.all_pages})
    return RunScope.model_validate({'requirements':requirements}),None


def format_result(run,evidence_store=None):
    """Small visible summary; exact observations remain in the owner-scoped run."""
    blocks=[];profiles={}
    for eid,payload in run['evidence'].items():
        if payload.get('status') not in ('ok','empty'):continue
        if evidence_store:payload=evidence_store.resolve(eid,payload)
        name=payload['tool'];data=payload['data']
        if name in REPORT_TOOLS:
            from tcm.research_report_tools import display
            block=display(eid,payload);report=data['report']
            block['evidence_reads']=[{'evidence_id':eid,'field':field,'count':len(values),
                'label':'共同靶点原始记录' if field=='items' else '字段原文与出处'} for field,values in (
                ('rows',report.get('hits',report.get('rows',[]))),('items',report.get('targets',{}).get('items',[]))) if values]
            if name=='herb_relation_records':
                block['evidence_reads']=[{'evidence_id':eid,'field':'items',
                                         'count':len(report.get('relations',{}).get('data',{}).get('items',[])),
                                         'label':'本页原始成分/靶点记录'}]
            blocks.append(block);continue
        if name=='search_disease_sources':continue
        if name=='disease_gene_profile':
            key=data['record_id']
            if key not in profiles:
                block={'tool':name,'title':data['profile']['name']+' · 疾病靶点',
                    'target_count':len(data['target_ids']),'rows':[],'evidence_ids':[],
                    'source':data['profile']['source'],'has_next':True}
                profiles[key]=block;blocks.append(block)
            block=profiles[key];block['rows'].extend(data['rows']);block['evidence_ids'].append(eid);block['has_next']=data['has_next']
        elif name in ('rank_herbs_for_gene_set','herb_target_similarity'):
            title=(data['profile']['name'] if name=='rank_herbs_for_gene_set' else data['profile']['source']['name'])
            blocks.append({'tool':name,'title':title+' · '+('药材候选' if name=='rank_herbs_for_gene_set' else '相似药材'),
                'metric':data['metric'],'requested_count':data['top_k'],'items':[
                    {'name':item['source']['name'],'source_record_id':item['source_record_id'],
                     'jaccard':item['jaccard'],'shared_count':item['shared_count'],
                     'shared_ids':item['shared_ids'],'source':item['source']['source'],
                     'evidence':item.get('evidence',[])} for item in data['items']],
                'source':data['profile']['source'],'evidence_ids':[eid]})
        elif name=='herb_target_profile':
            profile=data['profile']
            blocks.append({'tool':name,'title':profile['source']['name']+' · 靶点画像',
                'target_count':profile['target_count'],'target_ids':profile['target_ids'],
                'source':profile['source']['source'],'evidence_ids':[eid]})
    coverage=run.get('coverage',{})
    missing=[r['requirement_id'] for r in coverage.get('requirements',[]) if not r['fulfilled']]
    complete=run['status']=='complete'
    summaries=[]
    for block in blocks:
        if block.get('report_kind')=='pair':summaries.append(block['title']+'；共同靶点'+(str(block['shared_total'])+'个' if block['shared_total'] is not None else '未取得'))
        elif block.get('report_kind')=='relations':summaries.append(block['title']+'：'+str(block['total'])+'条记录，本页'+str(len(block['field_rows']))+'条')
        elif block.get('report_kind')=='fields':summaries.append(block['title']+'：'+str(len(block['field_rows']))+'条字段记录')
        elif block['tool']=='disease_gene_profile':summaries.append(f"{block['title'].split(' · ')[0]}：{block['target_count']}个靶点，已读取{len(block['rows'])}条原始记录")
        elif 'items' in block:summaries.append(f"{block['title']} {len(block['items'])}个")
        else:summaries.append(f"{block['title']}：{block['target_count']}个靶点")
    message='；'.join(summaries)+'。' if summaries else '未查到可展示的分析结果。'
    if not complete:message+=' 本次要求尚未全部完成。'
    for index,block in enumerate(blocks):
        refs=[eid for eid in block['evidence_ids'] if run['evidence'][eid].get('externalized')]
        if not refs:continue
        reads=[]
        for eid in refs:
            full=evidence_store.resolve(eid,run['evidence'][eid])
            data=full['data'];target_ids=data.get('target_ids',data.get('profile',{}).get('target_ids',[]))
            report=data.get('report',{})
            if full['tool']=='herb_relation_records':report=report.get('relations',{}).get('data',{})
            for field,values in (('target_ids',target_ids),('rows',data.get('rows',report.get('hits',report.get('rows')))),('items',data.get('items',report.get('targets',{}).get('items')))):
                if isinstance(values,list) and values:
                    entry={'evidence_id':eid,'field':field,'count':len(values)}
                    if full['tool'] in REPORT_TOOLS:entry['label']='共同靶点原始记录' if field=='items' else '字段原文与出处'
                    reads.append(entry)
        bounded=preview(block)
        if block.get('report_kind') and len(canonical(bounded).encode())>16000:
            bounded['shared_items']=[]
            bounded['preview_notice']='完整证据已保存；此处展示字段对照，靶点原始记录请按需读取或下载。'
        if len(canonical(bounded).encode())>16000:
            bounded={k:v for k,v in bounded.items() if k not in ('items','rows','target_ids','field_rows','shared_items')}
            bounded['preview_omitted']=True
        bounded.update(externalized=True,evidence_reads=reads,
            preview_notice='完整证据已保存；此处只显示预览，可分页查看或下载。')
        blocks[index]=bounded
    return response('ok' if complete else 'partial',message,blocks=blocks,
        incomplete_requirements=missing,stop_reason=run['reason'],
        execution={'mode':'local_declared_actions','driver_steps':run['model_calls'],'tool_calls':run['tool_calls'],
                   'action_calls':run.get('action_calls',run['tool_calls'])})


def finalize(result,plan,question,selections,stamp,identifier,evidence_store):
    rendered=format_result(result['run'],evidence_store);rendered['run_id']=identifier
    labels={'step-'+str(i):SPECS[step.call.tool][3]+'：'+(' / '.join(step.call.arguments.herbs) if step.call.tool=='pair_evidence_report' else step.call.arguments.name) for i,step in enumerate(plan.steps)}
    rendered['incomplete_requirements']=[labels.get(key,'疾病来源查找') for key in rendered['incomplete_requirements']]
    for i,step in enumerate(plan.steps):
        if step.call.tool not in ('search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set'):continue
        if any(str(j) in selections and s.call.arguments.name==step.call.arguments.name
               and s.call.tool in ('search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set') for j,s in enumerate(plan.steps)):continue
        for event in result['run']['events']:
            if event.get('tool')!='search_disease_sources' or event.get('arguments',{}).get('name')!=step.call.arguments.name:continue
            eid=event.get('evidence_id');payload=result['run']['evidence'].get(eid,{})
            payload=evidence_store.resolve(eid,payload)
            if payload.get('status')!='ok':continue
            data=payload['data']
            if not (data['total']==1 and not data['has_next'] and data['items'][0]['match_kind']=='exact' and data['items'][0]['name']==step.call.arguments.name):
                options=[{'id':r['record_id'],'label':r['name'],'source':r['source']} for r in data['items']]
                selected,pending_focus=pending(plan,question,selections,stamp,i,options)
                return {**rendered,'status':'partial','message':selected['message'],'options':selected['options']},pending_focus
    return rendered,{'kind':KIND,'plan':plan.model_dump(),'signature':stamp,'run_id':identifier}


async def execute(request,focus,*,owner,path):
    from features import env_bool
    if not env_bool('ENABLE_RESEARCH_TASKS'):return response('unavailable','本地多步骤分析尚未启用。'),None
    if request.get('replay'):
        return restore(focus,owner=owner,path=path) or response('clarification_needed','请先完成一次分析。'),focus
    doc=None;store=None
    try:
        selections={}
        if isinstance(focus,dict) and focus.get('job') and request.get('selection'):
            from tcm.research_plan_jobs import get as get_job
            prior=get_job(owner,focus['run_id'],path)
            focus=(prior.get('result') or {}).get('next_focus') or focus
        if request.get('selection'):
            if not isinstance(focus,dict) or focus.get('kind')!=KIND or 'pending_step' not in focus:
                return response('clarification_needed','当前会话没有待选择的分析来源，请重新输入问题。'),None
            choice=request['selection']
            if choice['step']!=focus['pending_step'] or choice['source_id'] not in focus['allowed_sources']:
                return response('clarification_needed','该来源不属于当前待选项，请从本次结果中选择。'),focus
            question=focus['question'];plan=validate_question(focus['plan'],question)
            if signature(plan)!=focus['signature']:raise SnapshotChanged('Source changed')
            selections={**focus['selections'],focus.get('selection_key',str(choice['step'])):choice['source_id']}
        else:
            question=request['question']
            plan=validate_question(request['plan'],question) if request.get('plan') else local_plan(question)
            if plan is None:return response('clarification_needed','尚未完整识别这句话中的分析要求。可明确疾病或药材名称，以及靶点资料、相似度或候选排序。'),None
        if any(s.call.tool in ('pharmacopoeia_lookup','pair_evidence_report') for s in plan.steps) and not env_bool('ENABLE_PHARMACOPOEIA_RESEARCH'):
            return response('unavailable','药典研究工具尚未启用，请先启用本地药典工具；本次没有执行查询。'),None
        stamp=signature(plan)
        scope,unresolved=compile_scope(plan,selections)
        if unresolved:
            step,options,key=unresolved
            if key=='same_source':return response('clarification_needed','两种名称对应同一份ETCM来源，请选择两味不同药材；本次尚未执行报告。'),None
            if not options:return response('clarification_needed','未找到该药材来源，请核对名称；本次尚未执行靶点分析。'),None
            return pending(plan,question,selections,stamp,step,options,key)
        from tcm.research_plan_jobs import enabled as jobs_enabled,make_payload,submit
        if jobs_enabled():
            rendered,new_focus,_=submit(owner,path,make_payload(question,plan,selections,stamp,scope),request.get('request_key'))
            return rendered,new_focus
        store=RunStore(path);doc=store.create(owner,question)
        evidence_store=for_run(path,owner,doc['id'])
        async def persist(snapshot):
            nonlocal doc
            doc=store.save(owner,doc,snapshot=snapshot)
        result=await run_local_actions(os.getenv('ETCM_DISEASE_DB'),scope,
            herbs=os.getenv('TCM_DB'),enrichment=os.getenv('ETCM_ENRICHMENT_DB'),on_state=persist,evidence_store=evidence_store,
            pharmacopoeia=os.getenv('PHARMACOPOEIA_INDEX'))
        if signature(plan)!=stamp:raise SnapshotChanged('Source changed')
        rendered,new_focus=finalize(result,plan,question,selections,stamp,doc['id'],evidence_store)
        store.save(owner,doc,result={**result,'display':rendered},status=result['status'])
        return rendered,new_focus
    except asyncio.CancelledError:
        if doc is not None:store.save(owner,doc,status='cancelled')
        raise
    except JobBusy:
        return response('busy','已有研究任务排队或执行中，或队列已满；本次没有创建新任务。可查看原任务进度或稍后重试。'),focus
    except (SnapshotChanged,KeyError,OSError,ValueError,sqlite3.Error,JobConflict):
        if doc is not None:
            try:store.save(owner,doc,status='failed')
            except Exception:pass
        return response('unavailable','本地来源或执行状态不可用，请重新查询；未使用模型补造结果。'),None


def restore(focus,*,owner,path):
    if not isinstance(focus,dict) or focus.get('kind')!=KIND:return None
    try:
        plan=AnalysisPlan.model_validate(focus['plan'])
        if signature(plan)!=focus['signature']:raise ValueError('Source changed')
        if 'pending_step' in focus:
            return response('clarification_needed','请选择上次分析要使用的来源，原问题已保留。',options=focus['options'])
        if focus.get('job'):
            from tcm.research_plan_jobs import get as get_job,view
            return view(get_job(owner,focus['run_id'],path))
        saved=RunStore(path).get(owner,focus['run_id'])['result']
        evidence_store=for_run(path,owner,focus['run_id'])
        with evidence_store.store.activity():evidence_store.view(saved['run']['evidence'])
        return saved['display']
    except (KeyError,OSError,ValueError,sqlite3.Error,JobConflict):
        return response('unavailable','上次分析来源或记录已改变，请重新查询。')
