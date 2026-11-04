"""Explicit D1 goals and bounded query specifications."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ATTRIBUTES = ('name_pinyin', 'latin_name', 'english_name', 'family', 'habitat',
              'medicinal_part', 'properties', 'flavors', 'meridians', 'efficacy_class', 'indications')
DOCUMENT_FIELDS = ('来源', '功能与主治', '注意', '贮藏', '炮制', '性味与归经', '用法与用量')
LABELS = {'来源': 'origin', **{x: x for x in DOCUMENT_FIELDS if x != '来源'}}
SID = r'^etcm2-local:[a-f0-9]{64}$'


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class AnswerDeliveryRequest(Contract):
    request_key: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


class Filter(Contract):
    field: str
    op: Literal['equals', 'contains', 'missing'] = 'contains'
    value: str = Field(default='', max_length=200)

    @model_validator(mode='after')
    def valid(self):
        if self.field not in ATTRIBUTES or (self.op != 'missing' and not self.value.strip()):
            raise ValueError('Choose a supported field and a nonblank value')
        if self.op == 'missing' and self.value:
            raise ValueError('Missing filter takes no value')
        return self


class Query(Contract):
    source_ids: list[str] = Field(default_factory=list, max_length=50)
    names: list[str] = Field(default_factory=list, max_length=50)
    filters: list[Filter] = Field(default_factory=list, max_length=5)
    group_by: str | None = None
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=5000)

    @model_validator(mode='after')
    def valid(self):
        import re
        if len(set(self.source_ids)) != len(self.source_ids) or any(not re.fullmatch(SID, s) for s in self.source_ids):
            raise ValueError('Invalid or duplicate source ID')
        if self.names and self.source_ids:
            raise ValueError('Choose names or source IDs, not both')
        if (any(not n.strip() or n != n.strip() or len(n) > 100 for n in self.names)
                or len({n.casefold() for n in self.names}) != len(self.names)):
            raise ValueError('Invalid or duplicate name')
        if self.group_by is not None and self.group_by not in ATTRIBUTES:
            raise ValueError('Unsupported grouping field')
        return self


class Goal(Contract):
    title: str = Field(default='批量资料核对', min_length=1, max_length=120)
    query: Query = Field(default_factory=Query)
    attributes: list[str] = Field(default_factory=lambda: ['medicinal_part', 'latin_name'], min_length=1, max_length=11)
    document_fields: list[str] = Field(default_factory=lambda: ['功能与主治', '注意'], min_length=1, max_length=4)
    completion_policy: Literal['audit', 'require_all_fields'] = 'audit'

    @model_validator(mode='after')
    def valid(self):
        if not self.title.strip():
            raise ValueError('Title is blank')
        for fields, allowed in ((self.attributes, ATTRIBUTES), (self.document_fields, DOCUMENT_FIELDS)):
            if len(set(fields)) != len(fields) or set(fields) - set(allowed):
                raise ValueError('Unknown or duplicate output fields')
        return self


class CreateWorkspace(Contract):
    goal: Goal
    request_key: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


class CreateAgent(CreateWorkspace):
    question: str = Field(min_length=1, max_length=2000)
    parent_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    expected_revision: int | None = Field(default=None, ge=1)
    refresh_source_ids: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode='after')
    def scope(self):
        Query(source_ids=self.refresh_source_ids)
        if not self.question.strip():
            raise ValueError('Question is blank')
        if bool(self.parent_id) != (self.expected_revision is not None):
            raise ValueError('Parent and expected revision must be provided together')
        return self


class Answer(Contract):
    job_revision: int = Field(ge=0)
    question_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(ge=1)
    operation: Literal['choose', 'skip', 'cancel']
    candidate_id: str | None = Field(default=None, max_length=200)


class Recover(Contract):
    job_revision: int = Field(ge=0)


class TaskQuestion(Contract):
    question: str = Field(min_length=1, max_length=2000)
    parent_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def scope(self):
        if not self.question.strip():
            raise ValueError('Question is blank')
        if bool(self.parent_id) != (self.expected_revision is not None):
            raise ValueError('Parent and revision must be provided together')
        return self


class SubmitQuestion(TaskQuestion):
    request_key: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
