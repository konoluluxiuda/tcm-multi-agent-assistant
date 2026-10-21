"""Bounded synthetic task plans. No free-text intent inference or model calls."""
from typing import Literal
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tcm.research_task_fixtures import NAMES


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal[1] = 1
    data_mode: Literal['synthetic_only'] = 'synthetic_only'
    intent: Literal['source_relations', 'evidence_review'] | None = None
    herb_one: Literal['示例甲草', '示例乙草'] | None = None
    herb_two: Literal['示例甲草', '示例乙草'] | None = None

    @model_validator(mode='after')
    def distinct_names(self):
        if self.herb_one is not None and self.herb_one == self.herb_two:
            raise ValueError('Choose two distinct fictional herbs')
        return self

    def missing(self):
        return [k for k in ('intent', 'herb_one', 'herb_two') if getattr(self,k) is None]

    def names(self):
        return [n for n in (self.herb_one,self.herb_two) if n is not None]

    def tools(self):
        base=['resolve_sources','common_targets']
        return base+(['search_literature','inspect_study'] if self.intent=='evidence_review' else [])+['finish_task']


INTENTS=[{'id':'source_relations','label':'只核对来源关系与双方原始记录'},
         {'id':'evidence_review','label':'继续核查已有合成研究记录'}]


class LocalTaskPlan(BaseModel):
    """Real names are allowed only in the local, read-only relation executor."""
    model_config=ConfigDict(extra='forbid',strict=True)
    version:Literal[1]=1
    data_mode:Literal['etcm_local']='etcm_local'
    intent:Literal['source_relations']|None=None
    herb_one:Annotated[str,Field(min_length=1,max_length=100)]|None=None
    herb_two:Annotated[str,Field(min_length=1,max_length=100)]|None=None

    @model_validator(mode='after')
    def distinct_names(self):
        names=self.names()
        if len(names)!=len(set(names)) or any(n!=n.strip() or not n.strip() for n in names):
            raise ValueError('Choose distinct, nonblank local names')
        return self

    missing=TaskPlan.missing
    names=TaskPlan.names
    tools=TaskPlan.tools


def plan_model(task):
    return LocalTaskPlan if task.get('data_mode')=='etcm_local' else TaskPlan


def clarification(plan, *, review=False, entities=None):
    labels={'intent':'本次任务范围','herb_one':'第一味合成药材','herb_two':'第二味合成药材'}
    intents=INTENTS
    names=NAMES
    if isinstance(plan,LocalTaskPlan):
        labels.update(herb_one='第一味本地药材',herb_two='第二味本地药材')
        intents=INTENTS[:1]
        names=entities or plan.names()
    return {'kind':'plan','options':[{'name':key,'label':labels[key],
            **({'suggested':getattr(plan,key)} if review else {}),
            'options':intents if key=='intent' else [{'id':n,'label':n} for n in names]}
            for key in (('intent','herb_one','herb_two') if review else plan.missing())]}


def confirm_plan(task, selection, *, accept_reduced_scope=False):
    """Fill exactly the saved missing slots; never alter confirmed ones."""
    model=plan_model(task)
    plan=model.model_validate(task['plan'])
    fields=('intent','herb_one','herb_two') if task.get('plan_review_required') else plan.missing()
    if (task['status']!='waiting_plan' or not isinstance(selection,dict)
            or set(selection)!=set(fields)):
        raise ValueError('Confirm exactly the missing plan fields')
    plan=model.model_validate({**plan.model_dump(),**selection})
    if plan.missing():raise ValueError('Plan still incomplete')
    if task.get('request_scope'):
        from tcm.request_coverage import confirm_scope
        confirm_scope(task,plan,accept_reduced_scope=accept_reduced_scope)
    elif accept_reduced_scope:raise ValueError('No declared request scope')
    task.update(plan=plan.model_dump(),names=plan.names(),pending=None,status='running')
    if task.get('plan_review_required'):task['plan_review_required']=False
    return task
