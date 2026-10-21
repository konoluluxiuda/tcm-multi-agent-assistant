"""Allowlisted, read-only ETCM task adapters. No model or network transport."""
from copy import deepcopy
from time import perf_counter
from typing import Any, Callable, Generic, Literal, TypeVar
import os
import sqlite3

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tcm.task_errors import LocalSnapshotChanged, LocalUnavailable


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ResolveSourcesArgs(Contract):
    names: list[str] = Field(min_length=2, max_length=2)


class CommonTargetsArgs(Contract):
    page: int = Field(ge=1, le=1000)


class NoArgs(Contract):pass


class SourceRecord(Contract):
    source_record_id: str
    source: dict[str, Any]


class SourceOption(Contract):
    id: str
    label: str
    source: dict[str, Any]


class SourceGroup(Contract):
    name: str
    options: list[SourceOption]
    resolution_status: str
    truncated: bool


class SourceResolution(Contract):
    groups: list[SourceGroup]
    records: list[SourceRecord]


class RelationWitness(Contract):
    source_id: str
    json_pointer: str = Field(pattern=r'^/')
    edge_id: str
    raw_row: dict[str, Any] = Field(min_length=1)
    capture_status: list[dict[str, Any]]
    source_row_count: int = Field(ge=1)
    witnesses_truncated: bool


class CommonTarget(Contract):
    id: str
    relation_id: str
    relation_scope: Literal['source_declared_common_target_not_mechanism']
    identity_status: str
    witnesses: list[RelationWitness] = Field(min_length=2)


class CommonTargets(Contract):
    total: int = Field(ge=0)
    page: int = Field(ge=1, le=1000)
    returned_count: int = Field(ge=0, le=20)
    has_next: bool
    items: list[CommonTarget]

    @model_validator(mode='after')
    def consistent_page(self):
        if (self.returned_count != len(self.items) or self.returned_count > self.total
                or (self.total == 0 and (self.page != 1 or self.has_next))
                or (self.total > 0 and not self.items)):
            raise ValueError('Inconsistent target page')
        return self


class ReferenceGroup(Contract):
    source_record_id: str
    source: dict[str, Any]
    total: int = Field(ge=0)
    returned_count: int = Field(ge=0, le=5)
    truncated: bool
    candidates: list[dict[str, Any]]

    @model_validator(mode='after')
    def consistent_count(self):
        if (self.returned_count != len(self.candidates) or self.returned_count > self.total
                or self.truncated != (self.total > self.returned_count)):
            raise ValueError('Inconsistent reference count')
        return self


class SourceReferences(Contract):
    version: Literal['task_literature_v1']
    status: Literal['local_ids_only']
    groups: list[ReferenceGroup]
    metadata_fetched: Literal[False]
    full_text_read: Literal[False]
    target_relation_verified: Literal[False]
    notice: str


ToolName = Literal['resolve_sources', 'common_targets', 'source_references']
ErrorCode = Literal['unknown_tool', 'invalid_arguments', 'invalid_state',
                    'unavailable', 'snapshot_changed', 'invalid_result']


class ToolError(Contract):
    code: ErrorCode


Payload = TypeVar('Payload', bound=BaseModel)


class ToolResult(Contract, Generic[Payload]):
    version: Literal[1] = 1
    tool: str
    status: Literal['ok', 'empty', 'error']
    data: Payload | None = None
    error: ToolError | None = None
    elapsed_ms: float = Field(ge=0)

    @model_validator(mode='after')
    def consistent_result(self):
        if (self.status == 'error') != (self.error is not None) or (self.data is None) != (self.status == 'error'):
            raise ValueError('Tool errors cannot contain successful data')
        return self


class InvalidToolState(ValueError):pass


# Fixed specifications; no dynamic import, user-provided callable or write tool.
_SPECS = {
    'resolve_sources': (ResolveSourcesArgs, SourceResolution, '查找本任务两味药的来源候选，必须由用户确认。'),
    'common_targets': (CommonTargetsArgs, CommonTargets, '分页读取已确认来源的共同标识及双方原始行。'),
    'source_references': (NoArgs, SourceReferences, '可选读取已完成任务的来源PMID，不获取文献。'),
}


def tool_specs():
    """Schemas only. Publishing these does not authorize model use of real data."""
    return [{'name': name, 'description': description, 'parameters': args.model_json_schema()}
            for name, (args, _, description) in _SPECS.items()]


class LocalTaskTools:
    def __init__(self, task: dict, ensure: Callable[[dict], None]):
        # A reader cannot mutate controller state or saved historical documents.
        self.task = deepcopy(task)
        self.ensure = ensure

    def invoke(self, name: str, arguments: dict) -> ToolResult:
        started = perf_counter()

        def error(code):
            return ToolResult(tool=name, status='error', error=ToolError(code=code),
                              elapsed_ms=(perf_counter()-started)*1000)

        if name not in _SPECS:
            return error('unknown_tool')
        args_type, output_type, _ = _SPECS[name]
        try:
            args = args_type.model_validate(arguments)
        except ValidationError:
            return error('invalid_arguments')
        try:
            if self.task.get('data_mode') != 'etcm_local':
                raise InvalidToolState()
            self.ensure(self.task)
            data = getattr(self, '_'+name)(args)
            self.ensure(self.task)
            payload = output_type.model_validate(data)
            empty = (any(not g.options for g in payload.groups) if name == 'resolve_sources'
                     else payload.total == 0 if name == 'common_targets'
                     else not any(g.candidates for g in payload.groups))
            return ToolResult[output_type](tool=name, status='empty' if empty else 'ok',
                                          data=payload, elapsed_ms=(perf_counter()-started)*1000)
        except InvalidToolState:
            return error('invalid_state')
        except LocalSnapshotChanged:
            return error('snapshot_changed')
        except ValidationError:
            return error('invalid_result')
        except (LocalUnavailable, sqlite3.Error, OSError, ValueError, KeyError):
            return error('unavailable')

    def _resolve_sources(self, args):
        from tcm.chat import execute_request
        if args.names != self.task['names'] or self.task['sources']:
            raise InvalidToolState()
        groups, records = [], []
        for name in args.names:
            result = execute_request({'source_record_id': name} if name.startswith('etcm2-local:') else {'name': name})
            if result['status'] in ('database_unavailable', 'dataset_not_found'):
                raise LocalUnavailable()
            if result.get('dataset_version') != self.task['snapshot']['dataset_version']:
                raise LocalSnapshotChanged()
            options = []
            for row in result['records']:
                records.append({'source_record_id': row['source_record_id'], 'source': row['source']})
                label = str(row['fields']['name_pinyin']['value'])+' · 药用部位：'+str(row['fields']['medicinal_part']['value'])
                options.append({'id': row['source_record_id'], 'label': label, 'source': row['source']})
            groups.append({'name': name, 'options': options, 'resolution_status': result['status'],
                           'truncated': result.get('truncated', False)})
        return {'groups': groups, 'records': records}

    def _common_targets(self, args):
        from tcm.relation_queries import shared_targets, LIMIT
        records = self.task.get('selected_source_records', [])
        ids = self.task['sources']
        if (len(ids) != 2 or len(set(ids)) != 2 or len(records) != 2
                or {r['source_record_id'] for r in records} != set(ids)
                or not set(ids) <= {r['source_record_id'] for r in self.task['source_options']}):
            raise InvalidToolState()
        data = shared_targets(os.environ['ETCM_ENRICHMENT_DB'], records,
                              self.task['snapshot']['dataset_version'], offset=(args.page-1)*LIMIT)
        if args.page > 1 and not data['items']:
            raise InvalidToolState()
        rows = []
        for row in data['items']:
            witnesses = [dict(source_id=source['source_record_id'], json_pointer=w['source_pointer'],
                              edge_id=w['edge_id'], raw_row=w['raw_row'], capture_status=w['capture_status'],
                              source_row_count=source['source_row_count'], witnesses_truncated=source['witnesses_truncated'])
                         for source in row['sources'] for w in source['witnesses']]
            rows.append({'id': row['identifier'], 'relation_id': row['node_id'],
                         'relation_scope': 'source_declared_common_target_not_mechanism',
                         'identity_status': row['identity_status'], 'witnesses': witnesses})
        return {'total': data['total'], 'page': args.page, 'returned_count': len(rows),
                'items': rows, 'has_next': data['next_offset'] is not None}

    def _source_references(self, args):
        from tcm.task_literature import reference_leads
        if not (self.task.get('report') or {}).get('local_steps_complete'):
            raise InvalidToolState()
        return reference_leads(self.task)
