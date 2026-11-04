"""Host-declared business requirements and evidence-derived disease dependencies."""
from pydantic import Field, model_validator
from tcm.task_tools import Contract
from tcm.research_business_tools import CALL,fulfilled

DISEASE_ACTIONS = ('disease_gene_profile', 'rank_herbs_for_gene_set')
HERB_ACTIONS = ('herb_target_profile', 'herb_target_similarity','herb_attributes','herb_relation_records')


class Requirement(Contract):
    id: str = Field(min_length=1, max_length=60)
    call: dict
    source_from: str | None = Field(default=None, min_length=1, max_length=60)
    all_pages: bool = False

    @model_validator(mode='after')
    def validate_call(self):
        call = self.call
        if self.source_from:
            if not isinstance(call.get('arguments', {}), dict):
                raise ValueError('Derived action arguments must be an object')
            if call.get('tool') not in DISEASE_ACTIONS or 'name' in call.get('arguments', {}):
                raise ValueError('Derived action must omit name and reference disease discovery')
            call = {**call, 'arguments': {**call.get('arguments', {}), 'name': 'etcm-disease:'+'0'*64}}
        parsed = CALL.validate_python(call)
        if parsed.tool=='pair_evidence_report' and not parsed.arguments.source_ids:
            raise ValueError('Pair scope requires host-selected source IDs')
        if parsed.tool in DISEASE_ACTIONS and not parsed.arguments.name.startswith('etcm-disease:'):
            raise ValueError('Profile scope requires a resolved source ID')
        if parsed.tool in HERB_ACTIONS and not parsed.arguments.name.startswith('etcm2-local:'):
            raise ValueError('Herb scope requires a resolved source ID')
        if self.all_pages and (parsed.tool != 'disease_gene_profile' or parsed.arguments.page != 1):
            raise ValueError('All pages requires a disease profile starting at page one')
        self.call = parsed.model_dump()
        if self.source_from:
            self.call['arguments'].pop('name')
        return self


class RunScope(Contract):
    requirements: list[Requirement] = Field(min_length=1, max_length=8)
    unhandled_requirements: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def distinct(self):
        by_id = {r.id: r for r in self.requirements}
        if len(by_id) != len(self.requirements):
            raise ValueError('Duplicate requirement ID')
        for r in self.requirements:
            if r.source_from:
                parent = by_id.get(r.source_from)
                if parent is None or parent.call['tool'] != 'search_disease_sources':
                    raise ValueError('Dependency must reference a disease search requirement')
                if not parent.call['arguments']['name'] or parent.call['arguments']['page'] != 1:
                    raise ValueError('Dependency requires a named search on the first page')
        if any(not s.strip() or len(s) > 500 for s in self.unhandled_requirements):
            raise ValueError('Invalid unhandled requirement')
        return self

    @staticmethod
    def observations(call, events, evidence, selected=None):
        selected = set(evidence if selected is None else selected)
        return [(e['evidence_id'], evidence[e['evidence_id']]) for e in events
                if e.get('evidence_id') in selected
                and evidence.get(e['evidence_id'], {}).get('status') in ('ok', 'empty')
                and call == {'tool': e['tool'], 'arguments': e.get('arguments')}]

    def resolved_call(self, requirement, events, evidence, selected=None):
        call = requirement.call
        if not requirement.source_from:
            return call
        parent = next(r for r in self.requirements if r.id == requirement.source_from)
        for _, payload in reversed(self.observations(parent.call, events, evidence, selected)):
            data = payload['data']
            if data['total'] == 1 and not data['has_next'] and len(data['items']) == 1:
                item = data['items'][0]
                if item['match_kind'] == 'exact' and item['name'] == parent.call['arguments']['name']:
                    return {'tool': call['tool'], 'arguments': {**call['arguments'], 'name': item['record_id']}}
        return None

    def progress(self, requirement, events, evidence, selected=None):
        call = self.resolved_call(requirement, events, evidence, selected)
        if call is None:
            return None, [], False
        ids = []
        while True:
            matches = self.observations(call, events, evidence, selected)
            if not matches:
                return call, ids, False
            eid, payload = matches[-1]
            ids.append(eid)
            if not fulfilled(call['tool'],payload):return call,ids,False
            if not requirement.all_pages or not payload['data']['has_next']:
                return None, ids, True
            call = {'tool': call['tool'], 'arguments': {**call['arguments'], 'page': call['arguments']['page']+1}}

    def permits(self, name, args, events=(), evidence=None):
        evidence = evidence or {}
        # A completed exact action remains in scope; the runtime rejects repeats.
        for r in self.requirements:
            call = self.resolved_call(r, events, evidence)
            if call == {'tool': name, 'arguments': args}:
                return True
            if r.all_pages:
                pending, _, _ = self.progress(r, events, evidence)
                if pending == {'tool': name, 'arguments': args}:
                    return True
        return False

    def pending(self, events, evidence):
        calls = []
        for r in self.requirements:
            call, _, complete = self.progress(r, events, evidence)
            if not complete and call and call not in calls:
                calls.append(call)
        return calls

    def coverage(self, events, evidence, selected=None):
        rows = []
        for r in self.requirements:
            _, ids, complete = self.progress(r, events, evidence, selected)
            rows.append({'requirement_id': r.id, 'fulfilled': complete, 'evidence_ids': ids})
        return {'requirements': rows, 'unhandled_requirements': self.unhandled_requirements,
                'complete': all(r['fulfilled'] for r in rows) and not self.unhandled_requirements}
