"""Versioned requirement contract (requirements-v2) for the understanding->planning chain.

The model produces a *draft* (objects as proposal-internal refs + literal
mentions, typed per-object requirements, delivery shape). The host then binds
names/sources/ownership into BoundRequirementsV2. Neither the draft nor the
bound result reads the original sentence; only the semantic stage does.

This module is data-only: no model, no tools, no question parsing.
"""
from typing import Literal, Union, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

VERSION = 'requirements-v2'


class FieldRef(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    namespace: Literal['etcm', 'pharmacopoeia']
    name: str = Field(min_length=1, max_length=60)

    @field_validator('name')
    @classmethod
    def nonblank(cls, v):
        if not v.strip() or v != v.strip():
            raise ValueError('field name must be non-blank and trimmed')
        return v


class ObjectRef(BaseModel):
    """Proposal-internal reference. `ref` is not a database id; `mention` is the
    literal surface text the user wrote."""
    model_config = ConfigDict(extra='forbid', strict=True)
    ref: str = Field(min_length=1, max_length=40, pattern=r'^o[0-9]+$')
    mention: str = Field(min_length=1, max_length=100)


class ReadFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^r[0-9]+$')
    kind: Literal['read_fields'] = 'read_fields'
    object_refs: list[str] = Field(min_length=1, max_length=20)
    fields: list[FieldRef] = Field(min_length=1, max_length=12)


class RelationReq(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^r[0-9]+$')
    kind: Literal['relation'] = 'relation'
    object_refs: list[str] = Field(min_length=1, max_length=2)
    relation_kind: Literal['ingredient', 'target', 'common_target']
    completeness: Literal['page', 'all'] = 'page'


class DatasetReq(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^r[0-9]+$')
    kind: Literal['dataset'] = 'dataset'
    datasets: list[Literal['etcm', 'pharmacopoeia']] = Field(default_factory=list, max_length=2)


Requirement = Annotated[Union[ReadFields, RelationReq, DatasetReq], Field(discriminator='kind')]


class AnswerDelivery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    form: Literal['answer']
    include_evidence: bool = True
    # Keep null defaults for stored v2 payload compatibility; the public schema
    # explicitly forbids non-null table parameters for answer delivery.
    row_policy: None = None
    count: None = None


class TableDelivery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    form: Literal['table']
    include_evidence: bool = True
    row_policy: Literal['keep_all', 'filter_complete', 'top_n']
    count: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode='after')
    def valid(self):
        if self.row_policy == 'top_n' and self.count is None:
            raise ValueError('top_n requires count')
        if self.row_policy != 'top_n' and self.count is not None:
            raise ValueError('count only allowed for top_n')
        return self


Delivery = Annotated[Union[AnswerDelivery, TableDelivery], Field(discriminator='form')]


class RequirementsDraftV2(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal['requirements-v2'] = 'requirements-v2'
    status: Literal['ready', 'needs_input']
    operation: Literal['new', 'append_fields']
    objects: list[ObjectRef] = Field(default_factory=list, max_length=20)
    requirements: list[Requirement] = Field(default_factory=list, max_length=8)
    delivery: Delivery
    unresolved: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def valid(self):
        ids = [r.id for r in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate requirement ids')
        refs = [o.ref for o in self.objects]
        if len(refs) != len(set(refs)):
            raise ValueError('Duplicate object refs')

        # needs_input carries only a clarification question; objects may be empty.
        if self.status == 'needs_input':
            if not self.unresolved:
                raise ValueError('needs_input requires unresolved')
            if self.requirements:
                raise ValueError('needs_input cannot carry executable requirements')
            return self

        # ready
        if self.unresolved:
            raise ValueError('ready cannot carry unresolved')
        if self.operation == 'append_fields' and self.delivery.form != 'table':
            raise ValueError('append_fields requires table delivery')
        if not self.requirements:
            raise ValueError('ready requires at least one executable requirement')
        has_dataset = any(r.kind == 'dataset' for r in self.requirements)
        if not self.objects and not has_dataset:
            raise ValueError('No objects requires a dataset-level requirement')
        ref_set = set(refs)
        for req in self.requirements:
            for ref in (getattr(req, 'object_refs', None) or []):
                if ref not in ref_set:
                    raise ValueError(f'Unknown object ref {ref}')
        return self


class BoundObject(BaseModel):
    """Host binding for one object. Source IDs, identity status and data version
    are host-owned; the model draft can never populate them."""
    model_config = ConfigDict(extra='forbid', strict=True)
    ref: str = Field(pattern=r'^o[0-9]+$')
    mention: str = Field(min_length=1, max_length=100)
    name_candidates: list[str] = Field(default_factory=list)
    selected_source_ids: list[str] = Field(default_factory=list)
    identity_verified: bool = False


class BoundRequirementsV2(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: Literal['requirements-v2'] = 'requirements-v2'
    draft: RequirementsDraftV2
    objects: list[BoundObject] = Field(default_factory=list, max_length=20)
    data_version: str = ''
    owner: str = ''
    parent_workspace_id: str | None = None
    parent_revision: int | None = None
    parent_goal: dict | None = None

    @model_validator(mode='after')
    def valid(self):
        draft_refs = {o.ref for o in self.draft.objects}
        bound_refs = {o.ref for o in self.objects}
        if bound_refs != draft_refs:
            raise ValueError('Bound objects must match draft object refs exactly')
        if len(self.objects) != len(bound_refs):
            raise ValueError('Duplicate bound object refs')
        return self
