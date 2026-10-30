"""Finite, fully consumed cross-source requests; no corpus or model calls."""
import re

PHARMA={'来源':'来源','基原':'来源','性味与归经':'性味与归经','性味':'性味与归经','归经':'性味与归经',
    '功能与主治':'功能与主治','功效':'功能与主治','主治':'功能与主治','功能':'功能与主治',
    '用法与用量':'用法与用量','用量':'用法与用量','用法':'用法与用量','剂量':'用法与用量',
    '注意事项':'注意','注意':'注意','禁忌':'注意','贮藏':'贮藏','保存':'贮藏','炮制':'炮制',
    '性状':'性状','鉴别':'鉴别','检查':'检查','含量测定':'含量测定'}
ATTR={'科名':'family','科':'family','药用部位':'medicinal_part','部位':'medicinal_part',
    '药性':'properties','药味':'flavors','归经':'meridians','产地':'habitat','功效分类':'efficacy_class',
    '适应证':'indications','文献ID':'reference_pmids','拉丁名':'latin_name','英文名':'english_name','拼音':'name_pinyin'}
NAME=r'([\u4e00-\u9fffA-Za-z0-9 .():_\-]{1,80}?)'


def clean(text):return re.sub(r'^(?:(?:请帮我|帮我|请|然后|并且|同时|并|再)\s*)+','',text).strip()


def fields(text,aliases,limit=4):
    names=sorted(aliases,key=len,reverse=True);pattern='|'.join(map(re.escape,names));result=[]
    remainder=re.sub(pattern,lambda m:result.append(aliases[m[0]]) or '',text)
    if re.sub(r'(?:和|与|及|、|的|以及|字段|原文|属性|信息|都|是什么|呢|\s)+','',remainder):return None
    result=list(dict.fromkeys(result))
    return result if 1<=len(result)<=limit else None


def leaf(text):
    from tcm.question_contract import field_tasks
    tasks=field_tasks(text)
    return tasks[0] if tasks and len(tasks)==1 else None


def pair(parts):
    start=None;args=None;covered=[];has_targets=False
    context=None
    for i,part in enumerate(parts):
        match=re.fullmatch(r'(?:我)?(?:正在|在)?(?:整理|研究|核对)'+NAME+r'(?:和|与|、)'+NAME+r'(?:的)?资料',clean(part))
        if match:
            if context is not None:return None
            context=(i,[match[1].strip(),match[2].strip()])
    for i,part in enumerate(parts):
        text=clean(part)
        match=re.fullmatch(r'(?:比较|对比|对照)?'+NAME+r'(?:和|与|、)'+NAME+r'的药典(?:的)?(.+)',text)
        contextual=re.fullmatch(r'(?:分别)?(?:比较|对比|对照|核对)药典(?:中|里)?(?:的)?(.+)',text) if context else None
        if contextual:
            if start is not None:return None
            requested=fields(contextual[1],PHARMA)
            if not requested:return None
            start=i;covered.extend([context[0],i])
            args={'herbs':context[1],'fields':requested,'attribute_fields':[],'all_targets':False}
            continue
        if match:
            if start is not None:return None
            rest=match[3]
            suffix=re.search(r'(?:并|和|以及)(?:核对|查询|查看|列出)(全部|所有)?共同靶点$',rest)
            if suffix:has_targets=True;rest=rest[:suffix.start()]
            requested=fields(rest,PHARMA)
            if not requested:return None
            start=i;covered.append(i)
            args={'herbs':[match[1].strip(),match[2].strip()],'fields':requested,
                  'attribute_fields':[],'all_targets':bool(suffix and suffix[1])}
    if start is None:return None
    for i,part in enumerate(parts):
        if i==start:continue
        text=clean(part)
        common=re.fullmatch(r'(?:核对|查询|查看|列出|比较|给出)(?:双方|两者|它们|这两味药)?(?:的)?(全部|所有)?共同(?:的)?(?:靶点|标识|UniProt)(?:和原始出处)?',text,re.I)
        if common:
            if has_targets:return None
            has_targets=True;args['all_targets']=bool(common[1]);covered.append(i)
        attr=re.fullmatch(r'(?:列出|查看|查询)(?:双方|它们|这两味药)(?:的)?ETCM(?:的)?(.+)',text,re.I)
        if attr:
            requested=fields(attr[1],ATTR)
            if not requested or args['attribute_fields']:return None
            args['attribute_fields']=requested;covered.append(i)
    return {'call':{'tool':'pair_evidence_report','arguments':args},'clauses':sorted(covered)} if has_targets else None


def validate_step(step,parts):
    """The model may propose only a completely grounded report contract."""
    from tcm.research_business_tools import CALL
    if step.call.tool=='pair_evidence_report':
        expected=pair(parts)
        if expected is None or sorted(step.clauses)!=expected['clauses']:raise ValueError('Uncovered pair requirements')
        call=CALL.validate_python(expected['call']).model_dump()
    else:
        if len(step.clauses)!=1:raise ValueError('One field clause per lookup')
        from tcm.question_contract import field_tasks,proposed_field_task
        tasks=field_tasks(parts[step.clauses[0]])
        if tasks:
            calls=[CALL.validate_python(task).model_dump() for task in tasks]
            if step.call.model_dump() not in calls:raise ValueError('Report object or fields changed')
            return
        if not proposed_field_task(parts[step.clauses[0]],step.call):raise ValueError('Ungrounded report requirement')
        return
    if step.call.model_dump()!=call:raise ValueError('Report object, fields or coverage changed')
