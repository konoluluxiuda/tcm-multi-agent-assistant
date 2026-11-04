"""Bounded slices of observations already owned by this execution state."""
from copy import deepcopy
import json
from typing import Literal
from pydantic import Field
from tcm.task_tools import Contract

NAME='read_run_evidence'


class ReadEvidence(Contract):
    evidence_id: str = Field(pattern=r'^obs-[1-9][0-9]*$',max_length=32)
    field: Literal['rows','target_ids','items','shared_ids','query_only_ids','candidate_only_ids','evidence']
    offset: int = Field(default=0,ge=0,le=100000)
    limit: int = Field(default=5,ge=1,le=20)
    item_index: int | None = Field(default=None,ge=0,le=19)


def schema():
    return {'type':'function','function':{'name':NAME,
        'description':'Read a bounded slice of an existing observation in THIS run, including privately externalized results. No source requery or network. Choose rows, target_ids or items; for candidate shared_ids/query_only_ids/candidate_only_ids/evidence select item_index (0-based). Offsets index this observation, not the source database. Follow next_offset; cite source_evidence_id for original provenance. Does not perform new research.',
        'parameters':ReadEvidence.model_json_schema()}}


def read(evidence,args):
    request=ReadEvidence.model_validate(args)
    source=evidence.get(request.evidence_id)
    def error(code):return {'tool':NAME,'status':'error','error':{'code':code}}
    from tcm.research_business_tools import SPECS,REPORT_TOOLS
    if not source or source.get('tool') not in SPECS:
        return error('unknown_source_evidence')
    if source.get('status') not in ('ok','empty'):return error('source_evidence_unavailable')
    original=source.get('data') or {}
    selected=original
    if source['tool'] in REPORT_TOOLS:
        selected=original.get('report') or {}
        if source['tool']=='pharmacopoeia_lookup' and request.field=='rows':
            selected={'rows':selected.get('hits',[])}
        elif source['tool']=='pair_evidence_report' and request.field=='items':
            selected=selected.get('targets',{})
        elif source['tool']=='herb_relation_records':
            selected=selected.get('relations',{}).get('data',{})
            if request.field=='rows':selected={'rows':selected.get('items',[])}
    base=selected
    if request.item_index is not None:
        candidates=base.get('items',[])
        if request.item_index>=len(candidates):return error('item_out_of_range')
        selected=candidates[request.item_index]
        if request.field not in ('shared_ids','query_only_ids','candidate_only_ids','evidence'):return error('field_not_available')
    elif request.field=='target_ids' and request.field not in selected:
        selected=selected.get('profile') or {}
    values=selected.get(request.field)
    if not isinstance(values,list):return error('field_not_available')
    if request.offset>len(values):return error('offset_out_of_range')
    data={'source_evidence_id':request.evidence_id,'source_tool':source['tool'],
          'source_binding':{k:original[k] for k in ('record_id','query','dataset_version','catalog_revision','page') if k in original},
          'field':request.field,'offset':request.offset,'total_in_observation':len(values),
          'items':[],'next_offset':None}
    if request.item_index is not None:data['item_index']=request.item_index
    if len(json.dumps(data,ensure_ascii=False).encode())>6000:return error('metadata_exceeds_read_budget')
    for value in values[request.offset:request.offset+request.limit]:
        candidate={**data,'items':data['items']+[value],'next_offset':request.offset+len(data['items'])+1}
        if len(json.dumps(candidate,ensure_ascii=False).encode())>6000:
            if not data['items']:return error('item_exceeds_read_budget')
            break
        data['items'].append(deepcopy(value))
    end=request.offset+len(data['items'])
    data['next_offset']=end if end<len(values) else None
    return {'tool':NAME,'status':'ok' if data['items'] else 'empty','data':data}
