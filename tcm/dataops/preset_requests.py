"""Whole-question parsing for the documented fixed query/table templates."""
import re
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from tcm.dataops.field_sources import parse_fields


def parse(question):
    if not isinstance(question, str) or len(question) > 500:
        return None
    from tcm.dataops.conversation_scope import subject
    parsed = subject(question)
    if parsed is None:
        return None
    names, body = parsed
    objects = [{'ref': f'o{i+1}', 'mention': n} for i,n in enumerate(names)]
    refs = [o['ref'] for o in objects]
    body = re.sub(r'(?:是什么|有哪些)$', '', body)
    if body in ('属性', '相关信息', '信息'):
        return RequirementsDraftV2(status='needs_input', operation='new', objects=objects,
            requirements=[], delivery={'form':'answer'},
            unresolved=['已记下'+ '、'.join(names)+'。需要哪些资料？可问性味、归经、科名、药用部位或药典功能与主治。'])
    relation = re.fullmatch(r'(成分|靶点)记录[，,]先显示一页', body)
    if relation and len(names) == 1:
        requirements = [{'id':'r1','kind':'relation','object_refs':refs,
                         'relation_kind':'ingredient' if relation[1]=='成分' else 'target','completeness':'page'}]
        delivery = {'form':'answer'}
    else:
        table = body.endswith('生成表格')
        if table:
            body = body[:-4]
        fields = parse_fields(body)
        if not fields:
            return None
        requirements = [{'id':'r1','kind':'read_fields','object_refs':refs,'fields':fields}]
        delivery = {'form':'table','row_policy':'keep_all'} if table else {'form':'answer'}
    return RequirementsDraftV2(status='ready',operation='new',objects=objects,
                               requirements=requirements,delivery=delivery)


def proposal(question, catalog):
    draft = parse(question)
    if draft is None:
        return None
    from tcm.dataops.requirements_pipeline import prepare_requirements
    from tcm.dataops.research_question import needs
    if draft.status == 'needs_input':
        return needs(question, '；'.join(draft.unresolved), compiler='preset_local_v1', calls=0)
    result = prepare_requirements(draft, catalog)
    if result['status'] != 'ready':
        return needs(question,'请核对完整药名及唯一来源；当前模板无法完整执行这些字段。',
                     compiler='preset_local_v1',calls=0)
    output = {'question':question,'compiler':'preset_local_v1','external_model_calls':0,
              'requirements':draft.model_dump(),'issues':[],'unresolved':[],
              'defaults':[],'refresh_source_ids':[]}
    if 'goal' in result:
        return {**output,'status':'ready','goal':result['goal']}
    for route in result['query_routes']:
        if route['tool']=='assistant' and route['request'].get('topic')=='etcm_relations':
            route['request']['resolve_unique_sources']=True
    return {**output,'status':'query','route':result['query_routes'][0],'routes':result['query_routes']}
