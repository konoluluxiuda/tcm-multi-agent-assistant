"""Deterministic prompt projection; full observations remain in run evidence.

UTF-8 serialized bytes are a local size guard, not provider-token accounting.
No tokenizer download, model summarization, or extra network request is used.
"""
import json
from langchain_core.messages import ToolMessage
from pydantic import Field, model_validator
from tcm.task_tools import Contract


class ContextPolicy(Contract):
    max_bytes: int = Field(default=64000,ge=1000,le=1000000)
    reserve_bytes: int = Field(default=8000,ge=0,le=100000)
    keep_recent_tools: int = Field(default=2,ge=1,le=16)

    @model_validator(mode='after')
    def room(self):
        if self.reserve_bytes>=self.max_bytes:raise ValueError('No input space')
        return self


def size(messages, tools):
    # Include native call arguments and tool definitions, not just text content.
    payload={'messages':[m.model_dump(mode='json') for m in messages],'tools':tools}
    return len(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8'))


def compact(message):
    payload=json.loads(message.content)
    data=payload.get('data') or {}
    summary={k:payload[k] for k in ('evidence_id','tool','status','error') if k in payload}
    summary['compacted']=True
    summary['detail_notice']='Historical observation summary; omitted fields are unknown here, not absent. Use read_run_evidence with the original evidence_id for available rows, target_ids or items; this summary does not prove omitted details.'
    summary['data']={k:data[k] for k in ('record_id','query','dataset_version','catalog_revision','page','page_size','has_next','total','source_evidence_id','source_tool','source_binding','field','offset','next_offset','total_in_observation') if k in data}
    if 'target_ids' in data:summary['data']['target_id_count']=len(data['target_ids'])
    if 'rows' in data:summary['data']['original_row_count_in_response']=len(data['rows'])
    if 'profile' in data:
        summary['data']['profile']={k:data['profile'][k] for k in ('name','target_count','species_status','detail_status') if k in data['profile']}
    if 'items' in data:
        summary['data']['items']=[{k:item[k] for k in ('record_id','name','target_count') if k in item} for item in data['items']]
    return message.model_copy(update={'content':json.dumps(summary,ensure_ascii=False,separators=(',',':'))})


def project(messages, tools, policy, *, counter=None, unit_limit=None):
    before=size(messages,tools);limit=policy.max_bytes-policy.reserve_bytes
    projected=list(messages);compacted=[]
    indices=[i for i,m in enumerate(messages) if isinstance(m,ToolMessage)]
    def over_budget(current):
        return size(current,tools)>limit or (counter is not None and unit_limit is not None and counter(current)>unit_limit)
    if over_budget(projected):
        for index in indices[:-policy.keep_recent_tools]:
            try:replacement=compact(messages[index])
            except (ValueError,TypeError,AttributeError):continue
            if size([replacement],[])>=size([messages[index]],[]):continue
            projected[index]=replacement
            compacted.append(messages[index].tool_call_id)
            if not over_budget(projected):break
    after=size(projected,tools)
    return projected,{'input_bytes_before':before,'input_bytes_after':after,'input_byte_limit':limit,
                      'compacted_call_ids':compacted,'fits':after<=limit,'measurement':'serialized_utf8_bytes_not_tokens'}
