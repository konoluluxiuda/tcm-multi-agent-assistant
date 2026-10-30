"""Reuse report subgraphs behind strict calls, source binding and a host leaf meter."""
import hashlib
import json
from tcm.research_business_tools import SPECS,REPORT_TOOLS
from tcm.task_tools import ToolResult

ATTRIBUTE_LABELS={'family':'科','medicinal_part':'药用部位','properties':'药性','flavors':'药味',
    'meridians':'归经','habitat':'产地','efficacy_class':'功效分类','indications':'适应证',
    'reference_pmids':'文献ID','latin_name':'拉丁名','english_name':'英文名','name_pinyin':'拼音'}


def field_label(value):return {'origin':'来源','性味':'性味与归经'}.get(value,value)
def attribute_label(value):return {'科名':'科','PMID':'文献ID'}.get(value,value)


def text_missing(result,name,fields):
    hits=result.get('hits',[])
    for hit in hits:
        if (hit.get('name')!=name or not hit.get('chunk_id') or not hit.get('text') or
                not hit.get('evidence') or any(not p.get('block_id') or not p.get('source') for p in hit['evidence'])):
            raise ValueError('Invalid pharmacopoeia provenance')
    observed={field_label(h['label']) for h in hits}
    missing=[f for f in fields if f not in observed]
    if result.get('status')!='candidates' and not missing:missing=['药典检索状态未完成']
    return missing


def attribute_missing(result,fields):
    rows=result.get('rows',[])
    if any(not r.get('source') or 'original_value' not in r for r in rows):
        raise ValueError('Invalid attribute provenance')
    observed={attribute_label(r['label']) for r in rows}
    missing=[ATTRIBUTE_LABELS[f] for f in fields if ATTRIBUTE_LABELS[f] not in observed]
    if result.get('status') not in ('ok','name_mapping_candidates') and not missing:missing=['ETCM来源未就绪']
    return missing


def missing_for(name,args,report):
    if name=='pharmacopoeia_lookup':return text_missing(report,args['name'],args['fields'])
    if name=='herb_attributes':
        if report.get('source_record_id') not in (None,args['name']):raise ValueError('Attribute source mismatch')
        return attribute_missing(report,args['fields'])
    from tcm.pair_research_report import source_pair,verify_targets
    if report.get('herbs')!=args['herbs'] or len(report.get('source_reports',[]))!=2:
        raise ValueError('Pair objects changed')
    missing=[]
    for herb,source,side in zip(args['herbs'],args['source_ids'],report['source_reports']):
        before=len(missing)
        if side.get('herb') not in (None,herb) or side.get('structured',{}).get('source_record_id') not in (None,source):
            raise ValueError('Pair source binding changed')
        missing.extend(herb+'：'+f for f in text_missing(side.get('text',{}),herb,args['fields']))
        missing.extend(herb+'：'+f for f in attribute_missing(side.get('structured',{}),args['attribute_fields']))
        if side.get('status')!='complete' and len(missing)==before:missing.append(herb+'：跨来源报告未完成')
    selection,reason=source_pair(report['source_reports']);target=report.get('targets',{})
    if reason or target.get('status')!='ok':missing.append('共同靶点：'+(reason or target.get('status','unavailable')))
    else:
        items=target.get('items',[])
        if len(items)>200 or len({i['identifier'] for i in items})!=len(items):raise ValueError('Invalid aggregate targets')
        for offset in range(0,max(1,len(items)),20):
            if verify_targets({**target,'items':items[offset:offset+20]},selection).get('status')!='ok':
                raise ValueError('Invalid target witnesses')
        if args['all_targets'] and (len(items)!=target.get('total') or target.get('next_offset') is not None):missing.append('共同靶点：分页未完成')
    if report.get('status')!='complete' and not missing:missing.append('两药报告未完成')
    return list(dict.fromkeys(missing))


def validate(name,args,raw):
    result=ToolResult[SPECS[name][1]].model_validate(raw)
    if result.tool!=name:raise ValueError('Report tool changed')
    if result.status!='error':
        data=result.data
        if data.request!=args:raise ValueError('Report request changed')
        missing=missing_for(name,args,data.report)
        if data.missing!=missing or data.complete!= (not missing):raise ValueError('Report completion changed')
    return result.model_dump()


async def execute(name,args,branch,charge,contract):
    from tcm.cross_source_report import run_report
    from tcm.pair_research_report import run_pair_report
    calls=0
    async def leaf(kind,herb,question,source_id=None):
        nonlocal calls
        charge();calls+=1
        return await branch(kind,herb,question,source_id=source_id)
    question='、'.join(args['fields']) if name!='herb_attributes' else ''
    if name=='pharmacopoeia_lookup':
        report=await leaf('text',args['name'],question)
    elif name=='herb_attributes':
        report=await leaf('structured',args['name'],'',source_id=args['name'])
    else:
        question+=('，全部共同靶点' if args['all_targets'] else '，共同靶点')
        sources=dict(zip(args['herbs'],args['source_ids']))
        async def researcher(payload):
            async def backend(kind,herb,query):return await leaf(kind,herb,query,source_id=sources[herb] if kind=='structured' else None)
            return await run_report(payload,backend=backend)
        async def targets(kind,herb,query):return await leaf(kind,herb,query)
        report=await run_pair_report({'herbs':args['herbs'],'question':question},researcher=researcher,target_backend=targets)
    missing=missing_for(name,args,report)
    revision=hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest()
    return {'tool':name,'status':'ok','elapsed_ms':0.0,'data':{'request':args,'complete':not missing,
        'missing':missing,'report':report,'dataset_version':str(contract.get('dataset_version') or 'local'),
        'catalog_revision':revision,'leaf_calls':calls}}


def display(eid,payload):
    name=payload['tool'];data=payload['data'];args=data['request'];report=data['report']
    base={'tool':name,'evidence_ids':[eid],'missing':data['missing'],'source':{
        'catalog_revision':data['catalog_revision'],'identity_merged':False}}
    if name=='pair_evidence_report':
        wanted=[r for r in report.get('rows',[]) if
            (r['source_type']=='药典' and field_label(r['field']) in args['fields']) or
            (r['source_type']=='ETCM' and attribute_label(r['field']) in [ATTRIBUTE_LABELS[f] for f in args['attribute_fields']])]
        rows=[]
        for row in wanted:
            shown={**row}
            for side in ('left','right'):
                shown[side]=[]
                for cell in row[side]:
                    original=cell['evidence']
                    proof=({'chunk_id':original['chunk_id'],'anchors':original['evidence'][:2],
                            'anchor_count':len(original['evidence'])} if row['source_type']=='药典' else original['source'])
                    shown[side].append({'value':cell['value'],'evidence':proof})
            rows.append(shown)
        target=report.get('targets',{})
        targets=[{'identifier':item['identifier'],'sources':[{'source_record_id':s['source_record_id'],
            'witnesses':s['witnesses']} for s in item['sources']]} for item in target.get('items',[])[:5]]
        return {**base,'report_kind':'pair','title':' / '.join(args['herbs'])+' · 资料对照',
            'herbs':args['herbs'],'field_rows':rows,'shared_items':targets,'target_preview':True,
            'shared_total':target.get('total'),'all_targets':args['all_targets'],
            'targets_complete':target.get('status')=='ok' and target.get('next_offset') is None,
            'source':{**base['source'],'sources':[s.get('structured',{}).get('source') for s in report['source_reports']]}}
    if name=='pharmacopoeia_lookup':
        rows=[{'field':field_label(h['label']),'value':h['text'],'evidence':h['evidence']} for h in report.get('hits',[]) if field_label(h['label']) in args['fields']]
        title=args['name']+' · 药典'
    else:
        rows=[{'field':attribute_label(r['label']),'value':r['value'],'original_value':r['original_value'],'evidence':r['source']} for r in report.get('rows',[]) if attribute_label(r['label']) in [ATTRIBUTE_LABELS[f] for f in args['fields']]]
        label=next((r['value'] for r in report.get('rows',[]) if r['label']=='拼音'),'药材')
        title=str(label)+' · ETCM属性'
    return {**base,'report_kind':'fields','title':title,'field_rows':rows}
