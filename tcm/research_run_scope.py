"""Caller-declared action scope. Does not infer complete intent from free text."""
from pydantic import Field, model_validator
from tcm.task_tools import Contract
from tcm.analysis_tools import CALL


class Requirement(Contract):
    id: str = Field(min_length=1,max_length=60)
    call: dict

    @model_validator(mode='after')
    def validate_call(self):
        parsed=CALL.validate_python(self.call)
        if parsed.tool not in ('search_disease_sources','disease_gene_profile'):
            raise ValueError('Unsupported research run action')
        if parsed.tool=='disease_gene_profile' and not parsed.arguments.name.startswith('etcm-disease:'):
            raise ValueError('Profile scope requires a resolved source ID')
        self.call=parsed.model_dump()
        return self


class RunScope(Contract):
    requirements: list[Requirement] = Field(min_length=1,max_length=8)
    unhandled_requirements: list[str] = Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def distinct(self):
        if len({r.id for r in self.requirements})!=len(self.requirements):
            raise ValueError('Duplicate requirement ID')
        if any(not s.strip() or len(s)>500 for s in self.unhandled_requirements):
            raise ValueError('Invalid unhandled requirement')
        return self

    def permits(self,name,args):
        return any(r.call=={'tool':name,'arguments':args} for r in self.requirements)

    def coverage(self,events,evidence,selected=None):
        selected=set(evidence if selected is None else selected)
        rows=[]
        for r in self.requirements:
            matches=[]
            for event in events:
                eid=event.get('evidence_id');payload=evidence.get(eid,{})
                if (eid in selected and payload.get('status') in ('ok','empty')
                        and r.call=={'tool':event['tool'],'arguments':event.get('arguments')}):
                    matches.append(eid)
            rows.append({'requirement_id':r.id,'fulfilled':bool(matches),'evidence_ids':matches})
        return {'requirements':rows,'unhandled_requirements':self.unhandled_requirements,
                'complete':all(r['fulfilled'] for r in rows) and not self.unhandled_requirements}
