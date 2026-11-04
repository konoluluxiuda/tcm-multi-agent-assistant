"""Capability registry: which field/relation x delivery-form is executable, and via which tool.

Upgraded from a static allow-list to an execution-consistency registry. Each
entry carries the real tool/compiler, object count range, runtime dependency,
and pagination facts so a planner can resolve a BoundRequirementsV2 into a
CompiledPlan without reading the original sentence.
"""
from typing import FrozenSet, Optional
from pydantic import BaseModel, ConfigDict

ETCM = 'etcm'
PHARMACOPOEIA = 'pharmacopoeia'

FORM_ANSWER = 'answer'
FORM_TABLE_CELL = 'table_cell'
FORM_RELATION = 'relation'
FORM_PAIR_REPORT = 'pair_report'
DELIVERY_FORMS = (FORM_ANSWER, FORM_TABLE_CELL, FORM_RELATION, FORM_PAIR_REPORT)

ETCM_ATTRIBUTES: FrozenSet[str] = frozenset({
    'name_pinyin', 'latin_name', 'english_name', 'family', 'habitat',
    'medicinal_part', 'properties', 'flavors', 'meridians',
    'efficacy_class', 'indications',
})
# Business query side also exposes reference_pmids (not a table cell).
ETCM_QUERY_ONLY: FrozenSet[str] = frozenset({'reference_pmids'})
ETCM_RELATIONS: FrozenSet[str] = frozenset({'ingredient', 'target', 'common_target'})
PHARMACOPOEIA_TABLE_FIELDS: FrozenSet[str] = frozenset({
    '来源', '功能与主治', '注意', '贮藏', '炮制', '性味与归经', '用法与用量',
})
# Business query side adds 性状/鉴别/检查/含量测定 (not table cells).
PHARMACOPOEIA_QUERY_ONLY: FrozenSet[str] = frozenset({'性状', '鉴别', '检查', '含量测定'})


class Capability(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str
    namespace: str
    name: str
    kind: str  # attribute | relation | document_field
    form: str
    tool: str
    object_min: int = 1
    object_max: int = 20
    runtime_dep: Optional[str] = None
    paginated: bool = False


def _registry() -> dict:
    reg = {}
    for field in ETCM_ATTRIBUTES:
        reg[(ETCM, field, FORM_ANSWER)] = Capability(
            id=f'{ETCM}.{field}.answer', namespace=ETCM, name=field, kind='attribute',
            form=FORM_ANSWER, tool='herb_attributes', object_min=1, object_max=1)
        reg[(ETCM, field, FORM_TABLE_CELL)] = Capability(
            id=f'{ETCM}.{field}.table_cell', namespace=ETCM, name=field, kind='attribute',
            form=FORM_TABLE_CELL, tool='dataops_table', object_min=1, object_max=20)
    for field in ETCM_QUERY_ONLY:
        reg[(ETCM, field, FORM_ANSWER)] = Capability(
            id=f'{ETCM}.{field}.answer', namespace=ETCM, name=field, kind='attribute',
            form=FORM_ANSWER, tool='herb_attributes', object_min=1, object_max=1)
    for rel in ETCM_RELATIONS:
        tool = 'assistant_etcm_relations' if rel == 'common_target' else 'herb_relation_records'
        obj_min = 2 if rel == 'common_target' else 1
        obj_max = 2 if rel == 'common_target' else 1
        reg[(ETCM, rel, FORM_RELATION)] = Capability(
            id=f'{ETCM}.{rel}.relation', namespace=ETCM, name=rel, kind='relation',
            form=FORM_RELATION, tool=tool, object_min=obj_min, object_max=obj_max, paginated=True)
        reg[(ETCM, rel, FORM_ANSWER)] = Capability(
            id=f'{ETCM}.{rel}.answer', namespace=ETCM, name=rel, kind='relation',
            form=FORM_ANSWER, tool=tool, object_min=obj_min, object_max=obj_max, paginated=True)
    for field in PHARMACOPOEIA_TABLE_FIELDS:
        reg[(PHARMACOPOEIA, field, FORM_ANSWER)] = Capability(
            id=f'{PHARMACOPOEIA}.{field}.answer', namespace=PHARMACOPOEIA, name=field,
            kind='document_field', form=FORM_ANSWER, tool='pharmacopoeia_lookup',
            object_min=1, object_max=1)
        reg[(PHARMACOPOEIA, field, FORM_TABLE_CELL)] = Capability(
            id=f'{PHARMACOPOEIA}.{field}.table_cell', namespace=PHARMACOPOEIA, name=field,
            kind='document_field', form=FORM_TABLE_CELL, tool='dataops_table',
            object_min=1, object_max=20)
        reg[(PHARMACOPOEIA, field, FORM_PAIR_REPORT)] = Capability(
            id=f'{PHARMACOPOEIA}.{field}.pair_report', namespace=PHARMACOPOEIA, name=field,
            kind='document_field', form=FORM_PAIR_REPORT, tool='pair_evidence_report',
            object_min=2, object_max=2)
    for field in PHARMACOPOEIA_QUERY_ONLY:
        reg[(PHARMACOPOEIA, field, FORM_ANSWER)] = Capability(
            id=f'{PHARMACOPOEIA}.{field}.answer', namespace=PHARMACOPOEIA, name=field,
            kind='document_field', form=FORM_ANSWER, tool='pharmacopoeia_lookup',
            object_min=1, object_max=1)
    return reg


REGISTRY = _registry()


def capability(namespace: str, name: str, form: str) -> bool:
    return (namespace, name, form) in REGISTRY


def deny_reason(namespace: str, name: str, form: str) -> str:
    allowed = sorted({k[2] for k in REGISTRY if k[0] == namespace and k[1] == name})
    joined = '、'.join(allowed) if allowed else '无'
    return f'{namespace}:{name} 不支持作为 {form} 交付；可用形式：{joined}。'


def resolve(namespace: str, name: str, form: str) -> Capability:
    """Return the registered capability or raise KeyError with a safe reason."""
    entry = REGISTRY.get((namespace, name, form))
    if entry is None:
        raise KeyError(deny_reason(namespace, name, form))
    return entry
