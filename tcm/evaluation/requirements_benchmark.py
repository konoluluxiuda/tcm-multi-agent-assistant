"""Source-bound grading of observed API deliveries; never generates observations."""
import hashlib
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

CATEGORIES = ('single_field', 'multi_field', 'cross_source', 'relation', 'dataset_info', 'table', 'boundary')
TERMINAL_STATES = ('success', 'partial', 'clarification', 'unsupported', 'unavailable', 'complete', 'failed', 'not_run')


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Turn(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    question: str = Field(min_length=1, max_length=2000)


class Requirement(Contract):
    kind: Literal['field', 'relation', 'dataset'] = 'field'
    object_ref: str | None = None
    object_refs: list[str] = Field(default_factory=list)
    namespace: Literal['etcm', 'pharmacopoeia', 'dataset']
    name: str = Field(min_length=1)

    @model_validator(mode='after')
    def valid(self):
        if self.namespace == 'dataset':
            self.kind = 'dataset'
            if self.object_ref or self.object_refs: raise ValueError('dataset has no object')
        elif self.kind == 'relation':
            if self.object_ref or not self.object_refs or len(set(self.object_refs)) != len(self.object_refs):
                raise ValueError('relation requires an explicit unique object group')
        elif not self.object_ref or self.object_refs:
            raise ValueError('field requires exactly one object')
        return self


class SourceAssertion(Contract):
    object_ref: str | None = None
    namespace: Literal['etcm', 'pharmacopoeia', 'dataset']
    name: str = Field(min_length=1)
    source_document: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    page: int | None = Field(default=None, ge=1)
    chunk_id: str | None = None
    total: int | None = Field(default=None, ge=0)
    returned_count: int | None = Field(default=None, ge=0)
    value_contains: str = Field(min_length=1)


class TableExpectation(Contract):
    row_policy: Literal['keep_all', 'filter_complete', 'top_n']
    count: int | None = Field(default=None, ge=1, le=5)
    parent_fields: list[str] = Field(default_factory=list)
    old_artifact_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @model_validator(mode='after')
    def valid(self):
        if (self.row_policy == 'top_n') != (self.count is not None): raise ValueError('count required only for top_n')
        return self


class Expected(Contract):
    task_class: Literal['supported', 'boundary']
    objects: list[str] = Field(default_factory=list)
    object_aliases: dict[str, list[str]] = Field(default_factory=dict)
    requirements: list[Requirement] = Field(default_factory=list)
    delivery: Literal['answer', 'table'] = 'answer'
    allowed_terminal_states: list[str] = Field(default_factory=lambda: ['success'])
    source_assertions: list[SourceAssertion] = Field(default_factory=list)
    missing_assertions: list[Requirement] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    table: TableExpectation | None = None

    @model_validator(mode='after')
    def valid(self):
        if not self.allowed_terminal_states or set(self.allowed_terminal_states)-set(TERMINAL_STATES): raise ValueError('invalid terminal states')
        if (self.delivery == 'table') != (self.table is not None): raise ValueError('table requires table expectation')
        if len(set(self.objects)) != len(self.objects): raise ValueError('duplicate object')
        if self.task_class == 'supported' and not self.requirements: raise ValueError('supported task requires requirements')
        if set(self.object_aliases)-set(self.objects): raise ValueError('alias owner not declared')
        owners={}
        for obj in self.objects:
            for alias in [obj,*self.object_aliases.get(obj,[])]:
                if alias in owners and owners[alias]!=obj: raise ValueError('ambiguous object alias')
                owners[alias]=obj
        refs = set(self.objects) | {f'o{i+1}' for i in range(len(self.objects))}
        for r in [*self.requirements, *self.missing_assertions]:
            if set(r.object_refs + ([r.object_ref] if r.object_ref else []))-refs: raise ValueError('unknown object ref')
        for a in self.source_assertions:
            if a.object_ref and a.object_ref not in refs: raise ValueError('unknown source object')
        return self


class Scenario(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    split: Literal['heldout', 'dev'] = 'dev'
    category: str
    entrypoint: Literal['chat', 'workbench'] = 'chat'
    turns: list[Turn] = Field(min_length=1, max_length=3)
    expected: Expected
    alternative_expectations: list[Expected] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def valid(self):
        if any(e.task_class != self.expected.task_class or e.delivery != self.expected.delivery or e.objects != self.expected.objects for e in self.alternative_expectations):
            raise ValueError('Alternatives must preserve task class, objects and delivery')
        if self.category not in CATEGORIES: raise ValueError('unknown category')
        if len({t.id for t in self.turns}) != len(self.turns): raise ValueError('duplicate turn ids')
        return self


class ResultStep(Contract):
    turn_id: str
    http_status: int
    terminal_state: str
    response_text: str = ''
    answer_steps: list[dict] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    fields: list[dict] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    relations: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    routes: list[dict] = Field(default_factory=list)
    request_id: str | None = None
    actual_policy: str | None = None
    artifact_path: str | None = None
    artifact_sha256: str | None = None
    table_result: dict | None = None
    parent_artifact_path: str | None = None
    parent_artifact_before: str | None = None
    parent_artifact_after: str | None = None
    duration_seconds: float = 0.0


class CaseResult(Contract):
    scenario_id: str
    policy: Literal['R', 'G', 'V']
    steps: list[ResultStep] = Field(default_factory=list)
    status: Literal['completed', 'failed', 'not_run', 'unknown'] = 'completed'
    error: str | None = None
    artifact_sha256: str | None = None  # compatibility; NOT sufficient to grade a file


class Score(Contract):
    scenario_id: str
    policy: str
    task_class: str
    completed: bool
    terminal_state: str
    dimensions: dict[str, bool] = Field(default_factory=dict)
    failures: list[str] = Field(default_factory=list)


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''): h.update(block)
    return h.hexdigest()


def _object(ref, expected):
    mapping={f'o{i+1}':name for i,name in enumerate(expected.objects)}
    value=mapping.get(ref,ref)
    for obj,aliases in expected.object_aliases.items():
        if value in aliases:return obj
    return value


def _key(item, expected):
    return (_object(item.get('object_ref'),expected), item.get('namespace'), item.get('name'))


def _source_matches(a, step, expected):
    wanted=a.model_dump(exclude_none=True)
    for src in step.sources:
        if _object(src.get('object_ref'),expected) != _object(a.object_ref,expected): continue
        if any(src.get(k)!=v for k,v in wanted.items() if k not in ('object_ref','value_contains')): continue
        if a.value_contains in str(src.get('value','')): return True
    return False


def _artifact(step, expected):
    from tcm.dataops.artifacts import verify
    if not step.artifact_path or not step.table_result: return False
    path=Path(step.artifact_path)
    if not path.is_file() or path.is_symlink() or digest(path)!=step.artifact_sha256: return False
    try: verify(path, step.table_result)
    except Exception: return False
    table=expected.table
    goal=step.table_result.get('goal',{})
    mode=goal.get('research',{}).get('mode') or step.table_result.get('research',{}).get('task_mode')
    if mode != {'keep_all':'audit_named','filter_complete':'filter_complete','top_n':'select_complete'}[table.row_policy]: return False
    if table.count is not None and step.table_result.get('research',{}).get('target_count',goal.get('research',{}).get('target_count'))!=table.count: return False
    if not set(table.parent_fields)<=set(goal.get('attributes',[])+goal.get('document_fields',[])): return False
    if table.parent_fields or table.old_artifact_sha256:
        if not step.parent_artifact_path or not step.parent_artifact_before or not step.parent_artifact_after: return False
        parent=Path(step.parent_artifact_path)
        if not parent.is_file() or parent.resolve()==path.resolve(): return False
        if not (digest(parent)==step.parent_artifact_after==step.parent_artifact_before): return False
        if table.old_artifact_sha256 and step.parent_artifact_before!=table.old_artifact_sha256: return False
    return True


def score(scenario, result):
    if scenario.alternative_expectations:
        # Each acceptable source is graded as a complete contract. Never combine
        # fields from incompatible alternatives to manufacture a passing answer.
        candidates = [score(scenario.model_copy(update={'expected':e,'alternative_expectations':[]}),result)
                      for e in [scenario.expected,*scenario.alternative_expectations]]
        return next((s for s in candidates if s.completed),candidates[0])
    e=scenario.expected; dims={}; final=result.steps[-1] if result.steps else None
    dims['identity']=scenario.id==result.scenario_id
    dims['turns']=[s.turn_id for s in result.steps]==[t.id for t in scenario.turns]
    dims['execution']=result.status=='completed'
    dims['terminal_state']=bool(final and final.terminal_state in e.allowed_terminal_states)
    dims['http']=bool(final) and all((200<=s.http_status<300 if s.terminal_state in ('success','complete','partial')
        else s.http_status in (200,400,403,422) if s.terminal_state in ('clarification','unsupported')
        else False) for s in result.steps)
    dims['parent_completion']=all(s.terminal_state in ('success','complete') for s in result.steps[:-1])
    if final and e.task_class=='supported':
        dims['objects']={_object(x,e) for x in final.objects}==set(e.objects)
        got={_key(x,e) for x in final.fields}
        relations={(r.get('namespace'),r.get('name'),tuple(sorted(_object(x,e) for x in r.get('object_refs',[])))) for r in final.relations}
        dims['requirements']=all((r.namespace,r.name,tuple(sorted(_object(x,e) for x in r.object_refs))) in relations if r.kind=='relation'
            else _key(r.model_dump(),e) in got for r in e.requirements)
        # A supported retrieval needs a witness for EVERY requested field/group.
        witness_keys={_key(a.model_dump(),e) for a in e.source_assertions}|{_key(r.model_dump(),e) for r in e.missing_assertions}
        dims['reference_coverage']=all(all((_object(ref,e),r.namespace,r.name) in witness_keys for ref in r.object_refs) if r.kind=='relation'
            else _key(r.model_dump(),e) in witness_keys for r in e.requirements)
        dims['sources']=all(_source_matches(a,final,e) for a in e.source_assertions)
        field_values={_key(x,e):x.get('value') for x in final.fields}
        relation_keys={(r.namespace,r.name) for r in e.requirements if r.kind=='relation'}
        dims['answer_values']=all((a.namespace,a.name) in relation_keys or
            a.value_contains in str(field_values.get(_key(a.model_dump(),e),'missing')) for a in e.source_assertions)
        missing={_key(x,e) for x in final.fields if x.get('value') is None and x.get('state') in ('missing','not_recorded','field_missing','missing_field')}
        dims['missing']=all(_key(r.model_dump(),e) in missing for r in e.missing_assertions)
        if e.delivery=='table': dims['table']=_artifact(final,e)
    text=' '.join(s.response_text for s in result.steps)
    dims['forbidden_claims']=not any(c in text for c in e.forbidden_claims)
    return Score(scenario_id=scenario.id,policy=result.policy,task_class=e.task_class,
        completed=all(dims.values()),terminal_state=final.terminal_state if final else result.status,
        dimensions=dims,failures=[k for k,v in dims.items() if not v])


def load_scenarios(text):
    raw=json.loads(text)
    if not isinstance(raw,list) or not raw: raise ValueError('nonempty scenario list required')
    items=[Scenario.model_validate(i) for i in raw]
    if len({s.id for s in items})!=len(items): raise ValueError('duplicate scenario ids')
    return items
