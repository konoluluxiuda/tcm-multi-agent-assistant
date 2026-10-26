"""One validated read-only contract for direct and MCP report tools."""
import asyncio
import json
from pydantic import BaseModel, Field, ValidationError, field_validator


class HerbArgs(BaseModel):
    model_config={'extra':'forbid','strict':True}
    herb: str = Field(min_length=1,max_length=80)

    @field_validator('herb')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():raise ValueError('Blank herb')
        return value.strip()


class TextArgs(HerbArgs):
    question: str = Field(min_length=1,max_length=400)

    @field_validator('question')
    @classmethod
    def nonblank_question(cls,value):
        if not value.strip():raise ValueError('Blank question')
        return value.strip()


TOOLS={
    'lookup_pharmacopoeia':(TextArgs,'text','查询本地药典字段及出处；不是诊疗建议，不生成答案。'),
    'lookup_etcm_profile':(HerbArgs,'structured','查询本地ETCM药材属性及出处；同名候选不等于身份核验。'),
}
BRANCH_TO_TOOL={value[1]:name for name,value in TOOLS.items()}


def specifications():
    return [{'name':name,'description':description,'inputSchema':args.model_json_schema()}
            for name,(args,_,description) in TOOLS.items()]


async def invoke(name, arguments, *, backend=None):
    def error(code):return {'tool':name,'status':'error','error':code}
    if name not in TOOLS:return error('unknown_tool')
    args_type,branch,_=TOOLS[name]
    try:args=args_type.model_validate(arguments)
    except ValidationError:return error('invalid_arguments')
    if backend is None:
        from tcm.cross_source_report import local_branch
        backend=local_branch
    try:
        result=await backend(branch,args.herb,getattr(args,'question',''))
        if not isinstance(result,dict) or not isinstance(result.get('status'),str):return error('invalid_result')
        if len(json.dumps(result,ensure_ascii=False).encode())>400000:return error('result_too_large')
        return {'tool':name,'status':'ok','data':result}
    except asyncio.TimeoutError:return error('timeout')
    except Exception:return error('unavailable')
    # CancelledError propagates; never retry an interrupted operation.
