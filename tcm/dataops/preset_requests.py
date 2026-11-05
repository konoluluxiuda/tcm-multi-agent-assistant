"""Whole-question parsing for the documented fixed query/table templates."""
import re
from tcm.dataops.requirements_v2 import RequirementsDraftV2

FIELDS = {
    'etcm': {'科名': ['family'], '药用部位': ['medicinal_part'],
             '性味': ['properties', 'flavors'], '归经': ['meridians']},
    'pharmacopoeia': {'功能与主治': ['功能与主治'], '用法与用量': ['用法与用量']},
}


def parse(question):
    if not isinstance(question, str) or len(question) > 500:
        return None
    text = question.strip().rstrip('。.!！?？')
    match = re.fullmatch(r'(?:查看|查询|分别告诉我|分别查询|把)(.+?)的(.+)', text)
    if not match:
        return None
    names = re.split(r'[、和与]', match[1])
    if not 1 <= len(names) <= 5 or len(set(names)) != len(names):
        return None
    if any(not re.fullmatch(r'[\u4e00-\u9fffA-Za-z]{2,30}', n) for n in names):
        return None
    objects = [{'ref': f'o{i+1}', 'mention': n} for i,n in enumerate(names)]
    refs = [o['ref'] for o in objects]
    body = match[2]
    relation = re.fullmatch(r'(成分|靶点)记录[，,]先显示一页', body)
    if relation and len(names) == 1:
        requirements = [{'id':'r1','kind':'relation','object_refs':refs,
                         'relation_kind':'ingredient' if relation[1]=='成分' else 'target','completeness':'page'}]
        delivery = {'form':'answer'}
    else:
        table = body.endswith('生成表格')
        if table:
            body = body[:-4]
        fields = []
        while body:
            source = re.match(r'(ETCM|药典)', body)
            if not source:
                return None
            ns = 'etcm' if source[1]=='ETCM' else 'pharmacopoeia'
            body = body[len(source[1]):]
            found = False
            while body:
                label = next((k for k in sorted(FIELDS[ns],key=len,reverse=True) if body.startswith(k)),None)
                if label is None:
                    return None
                found = True
                fields.extend({'namespace':ns,'name':f} for f in FIELDS[ns][label])
                body = body[len(label):]
                if not body:
                    break
                if body[0] not in '、和':
                    return None
                body = body[1:]
                if not body:
                    return None
                if body.startswith(('ETCM','药典')):
                    break
            if not found:
                return None
        if not fields or len({(f['namespace'],f['name']) for f in fields}) != len(fields):
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
