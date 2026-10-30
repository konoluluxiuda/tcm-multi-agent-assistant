"""Question-only proposals for bounded multi-step structural analysis."""
import re
from collections import Counter
from pydantic import Field, model_validator
from tcm.task_tools import Contract
from tcm.research_business_tools import BusinessCall,REPORT_TOOLS


class PlanStep(Contract):
    call: BusinessCall
    clauses: list[int] = Field(min_length=1, max_length=12)
    all_pages: bool = False

    @model_validator(mode='after')
    def pages(self):
        if self.all_pages and (self.call.tool!='disease_gene_profile' or self.call.arguments.page!=1):
            raise ValueError('Full pages requires disease profile page one')
        return self


class AnalysisPlan(Contract):
    steps: list[PlanStep] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def compound(self):
        return self


def clauses(question):
    from tcm.question_contract import clauses as split
    return split(question)


def clean(text):
    return re.sub(r'^(?:(?:请帮我|帮我|请|然后|并且|同时|并|再)\s*)+','',text).strip()


def presentation(text):
    return bool(re.fullmatch(r'(?:附上|附|保留|给出|显示)(?:双方|对应|各项|所有|每项)?'
        r'(?:出处|来源|依据|原始记录(?:(?:的)?位置)?)(?:(?:和|与|及)(?:出处|来源|依据|原始记录(?:(?:的)?位置)?))*',clean(text).replace('各自','对应')))


def validate_question(plan,question):
    """Ground objects and account for every clause; not a semantic quality judge."""
    from tcm.intent_planner import grounded
    from tcm.request_boundaries import unsupported_requirement
    plan=AnalysisPlan.model_validate(plan)
    parts=clauses(question)
    if not 1<=len(parts)<=12 or unsupported_requirement(question) or re.search(r'文献(?!ID)|论文|采购|价格|写诗|写一首诗|预测|治疗|疗效|配药|处方|导出|下载',question):raise ValueError('Unsupported mixed request')
    covered=set()
    for step in plan.steps:
        if len(set(step.clauses))!=len(step.clauses) or any(i<0 or i>=len(parts) for i in step.clauses):
            raise ValueError('Invalid clause reference')
        if step.call.tool in REPORT_TOOLS:
            from tcm.research_report_plan import validate_step
            validate_step(step,parts)
            names=step.call.arguments.herbs if step.call.tool=='pair_evidence_report' else [step.call.arguments.name]
            if any(not grounded(n,question) for n in names):raise ValueError('Ungrounded report object')
            covered.update(step.clauses);continue
        name=step.call.arguments.name
        if not name or not grounded(name,question):raise ValueError('Ungrounded object')
        own_text='，'.join(parts[i] for i in step.clauses)
        if '药典' in own_text:raise ValueError('Pharmacopoeia requirement is not a target analysis')
        if not grounded(name,own_text):
            disease_names={s.call.arguments.name for s in plan.steps
                if s.call.tool in ('search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set')}
            earlier=any(s is not step and s.call.arguments.name==name and max(s.clauses)<min(step.clauses)
                and s.call.tool in ('search_disease_sources','disease_gene_profile') for s in plan.steps)
            if (step.call.tool not in ('disease_gene_profile','rank_herbs_for_gene_set')
                    or disease_names!={name} or not earlier or not re.search(r'靶点|候选',own_text)):
                raise ValueError('Object does not belong to this request clause')
        # The intent model may copy literal IDs; it cannot invent catalog IDs.
        for i in step.clauses:
            if presentation(parts[i]):raise ValueError('Display clause is not a business action')
            if re.search(r'(?:全部|所有|完整).*(?:原始记录|原始行)',parts[i]) and not step.all_pages:
                raise ValueError('Full original-row requirement lost')
            if re.search(r'(?:候选|相似|近邻)',parts[i]) and step.call.tool not in ('herb_target_similarity','rank_herbs_for_gene_set'):
                raise ValueError('Candidate requirement lost')
            numbers=re.findall(r'前\s*([0-9]+|[一二三四五六七八九十两]+)\s*(?:个|条|名|项)',parts[i])
            if numbers and (len(numbers)!=1 or count(numbers[0])!=getattr(step.call.arguments,'top_k',None)):
                raise ValueError('Requested count changed')
            if re.search('共同靶点数',parts[i]) and getattr(step.call.arguments,'metric',None)!='common_count':
                raise ValueError('Requested metric changed')
            if re.search(r'Jaccard',parts[i],re.I) and getattr(step.call.arguments,'metric',None)!='jaccard':
                raise ValueError('Requested metric changed')
        page=getattr(step.call.arguments,'page',1)
        if page!=1 and not re.search(r'第\s*'+str(page)+r'\s*页|page\s+'+str(page)+r'\b',own_text,re.I):
            raise ValueError('Unrequested page')
        covered.update(step.clauses)
    if covered|{i for i,p in enumerate(parts) if presentation(p)}!=set(range(len(parts))):
        raise ValueError('Unaccounted request clauses')
    # Clause coverage alone cannot detect a missing second object in one
    # coordinated sentence. Check its complete field-task multiset as well.
    from tcm.question_contract import field_tasks
    from tcm.research_business_tools import CALL
    for i,part in enumerate(parts):
        if any(step.call.tool=='pair_evidence_report' and i in step.clauses for step in plan.steps):continue
        expected=field_tasks(part)
        if not expected:continue
        key=lambda call:call.model_dump_json()
        wanted=Counter(key(CALL.validate_python(call)) for call in expected)
        actual=Counter(key(step.call) for step in plan.steps if i in step.clauses and step.call.tool in ('herb_attributes','pharmacopoeia_lookup'))
        if actual!=wanted:raise ValueError('Field requirements omitted or duplicated')
    return plan


def count(value):
    if value.isdecimal():return int(value)
    numbers={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
    if value in numbers:return numbers[value]
    if value.startswith('十') and value[1:] in numbers:return 10+numbers[value[1:]]
    if '十' in value:
        tens,units=value.split('十',1)
        if tens in numbers and (not units or units in numbers):return numbers[tens]*10+(numbers[units] if units else 0)
    raise ValueError('Unsupported count')


def local_plan(question):
    """Fully consumed local grammar. No database lookup or external request."""
    parts=clauses(question)
    if not 1<=len(parts)<=12:return None
    from tcm.research_report_plan import pair as pair_plan,leaf as report_leaf
    paired=pair_plan(parts)
    steps=[];disease=None
    if paired:steps.append(PlanStep.model_validate(paired))
    name=r'([\u4e00-\u9fffA-Za-z0-9 .():_\-]{1,100}?)'
    for i,part in enumerate(parts):
        if paired and i in paired['clauses']:continue
        text=clean(part)
        if presentation(part):continue
        from tcm.question_contract import field_tasks
        reports=field_tasks(text)
        if reports:
            steps.extend(PlanStep(call=report,clauses=[i]) for report in reports);continue
        modifier=re.fullmatch(r'(?:显示|列出|列|返回)?前([0-9]+|[一二三四五六七八九十两]+)(?:个|条|项)(?:药材|来源)?',text)
        metric_only=re.fullmatch(r'按(Jaccard(?:相似度)?|共同靶点数)排序',text,re.I)
        if (modifier or metric_only) and steps and steps[-1].call.tool in ('herb_target_similarity','rank_herbs_for_gene_set'):
            raw=steps[-1].model_dump();raw['clauses'].append(i)
            if modifier:raw['call']['arguments']['top_k']=count(modifier[1])
            else:raw['call']['arguments']['metric']='common_count' if metric_only[1]=='共同靶点数' else 'jaccard'
            try:steps[-1]=PlanStep.model_validate(raw)
            except ValueError:return None
            continue
        search=re.fullmatch(r'(?:查找|搜索|检索)(?:疾病\s*)?(.{1,150}?)(?:的)?(?:疾病)?来源(?:ID)?',text,re.I)
        if not search:search=re.fullmatch(r'(?:查找|搜索|检索)疾病(?:名称)?\s*[:：]?\s*'+name,text)
        named_profile=re.fullmatch(r'(?:读取|查看|查询)疾病\s*(.{1,150}?)的(全部|所有|完整)?靶点(?:的)?(?:原始记录|原始行|资料|画像)',text)
        profile=re.fullmatch(r'(?:读取|查看|查询)(?:(?:该|这个)?疾病(?:的)?\s*)?'
            r'(?:(全部|所有|完整)的?)?靶点(?:的)?(?:原始记录|原始行|资料|画像)',text)
        rank=re.fullmatch(r'按\s*(Jaccard(?:相似度)?|共同靶点数)\s*(?:排序)?'
            r'(?:列出|列|显示|给出)?前\s*([0-9]+|[一二三四五六七八九十两]+)\s*(?:个|条)(?:药材)?候选(?:药材)?',text,re.I)
        if named_profile:
            disease=named_profile[1].strip();tool='disease_gene_profile';args={'name':disease};full=bool(named_profile[2])
        elif search:
            disease=search[1].strip();tool='search_disease_sources';args={'name':disease};full=False
        elif profile and disease:
            tool='disease_gene_profile';args={'name':disease};full=bool(profile[1])
        elif rank and disease:
            tool='rank_herbs_for_gene_set';args={'name':disease,'top_k':count(rank[2]),
                'metric':'common_count' if rank[1]=='共同靶点数' else 'jaccard'};full=False
        else:
            from tcm.analysis_chat import parse
            parsed=parse(text)
            if not parsed or 'call' not in parsed or parsed.get('followup'):return None
            tool=parsed['call']['tool'];args=parsed['call']['arguments'];full=False
            if tool in ('search_disease_sources','disease_gene_profile','rank_herbs_for_gene_set'):disease=args['name']
        try:steps.append(PlanStep(call={'tool':tool,'arguments':args},clauses=[i],all_pages=full))
        except ValueError:return None
    if not steps:return None
    try:return validate_question(AnalysisPlan(steps=steps),question)
    except ValueError:return None


def capture(question):
    """Route target-analysis compounds before a permissive single-query parser."""
    if not isinstance(question,str) or question.startswith('/') or not 1<=len(question)<=1000:return None
    from features import env_bool
    if not env_bool('ENABLE_RESEARCH_TASKS'):return None
    mixed_pair='药典' in question and bool(re.search(r'共同靶点|共同标识|共同UniProt',question,re.I))
    if not mixed_pair and re.search(r'共同靶点(?!数)|共同标识|共同UniProt|靶点交集|UniProt交集|共有靶点',question,re.I):return None
    parts=clauses(question)
    # Preserve established single-field drug and follow-up routes. Structural
    # multi-fields and explicit full disease rows need the bounded A executor.
    from tcm.question_contract import field_tasks
    one=field_tasks(parts[0]) if len(parts)==1 else None
    single_contract=bool(one and (len(one)>1 or any(len(c['arguments']['fields'])>1 for c in one))) or bool(re.search(r'疾病.*(?:全部|所有|完整).*原始(?:行|记录)',question))
    # An explicitly named source with an unfamiliar field sentence must reach
    # typed intent validation, rather than the broad pharmacopoeia-keyword path.
    source_proposal=bool(re.search(r'ETCM|药典',question,re.I) and re.search(r'关于|告诉我|想知道|想了解',question) and one is None)
    single_contract=single_contract or source_proposal
    if not mixed_pair and not single_contract and (len(parts)<2 or not re.search(r'靶点|候选|近邻|Jaccard|药典|ETCM',question,re.I)):return None
    try:plan=local_plan(question)
    except ValueError:plan=None
    # Leave established single analyses with display/count modifiers alone.
    actions=[p for p in parts if not presentation(p) and not re.fullmatch(
        r'(?:显示|列出|列|返回)?前[0-9一二三四五六七八九十两]+(?:个|条)(?:药材|来源)?|按(?:共同靶点数|Jaccard(?:相似度)?)排序',clean(p),re.I)]
    if plan is None:
        if mixed_pair:return {'topic':'research_plan','question':question,'plan':None}
        from tcm.analysis_chat import parse
        legacy=parse(question)
        if legacy and legacy.get('call',{}).get('tool') in ('herb_target_profile','herb_target_similarity'):return None
    if plan is None and len(actions)<2 and not single_contract:return None
    return {'topic':'research_plan','question':question,'plan':plan.model_dump() if plan else None}
