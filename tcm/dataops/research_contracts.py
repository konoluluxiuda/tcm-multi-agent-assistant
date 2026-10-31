"""R4 bounded selection goal. Does not change the legacy D1/D2 schema."""
from pydantic import Field,model_validator
from typing import Literal
from tcm.dataops.contracts import Contract,Goal,CreateAgent,SubmitQuestion


class Selection(Contract):
    mode:Literal['select_complete','audit_named']='select_complete'
    target_count:int=Field(default=2,ge=1,le=5)
    monograph_only:bool=False


class ResearchGoal(Goal):
    research:Selection=Field(default_factory=Selection)

    @model_validator(mode='after')
    def bounded_pool(self):
        if self.query.limit>20 or self.query.group_by or self.research.target_count>self.query.limit:
            raise ValueError('Choose up to 20 candidates and a feasible target of 1–5')
        if not (self.query.names or self.query.source_ids or self.query.filters):
            raise ValueError('An explicit candidate set or structured filter is required')
        if self.completion_policy!='audit':raise ValueError('This profile preserves unverified source scope')
        if self.research.mode=='audit_named' and (self.query.filters or self.query.offset or not (self.query.names or self.query.source_ids)):
            raise ValueError('Named audit requires an explicit unpaged record set')
        return self

    def legacy(self):
        return Goal.model_validate(self.model_dump(exclude={'research'}))


class ResearchCreate(CreateAgent):
    goal:ResearchGoal


class ResearchQuestion(SubmitQuestion):
    use_model:bool=False


def same_research_scope(parent,goal):
    old=ResearchGoal.model_validate(parent)
    new=ResearchGoal.model_validate(goal)
    return (old.query==new.query and old.research==new.research and
            set(old.document_fields)<=set(new.document_fields) and set(old.attributes)<=set(new.attributes))
