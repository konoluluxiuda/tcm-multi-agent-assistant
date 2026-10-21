"""Versioned completion contract for local source queries, independent of research claims."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,model_validator

Step=Literal['source_selection','common_target_query','selected_evidence_check']


class TaskCompletion(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    version:Literal[2]=2
    scope:Literal['local_relation']='local_relation'
    status:Literal['complete','partial']
    outcome:Literal['matched','empty','source_unresolved','incomplete']
    required_steps:list[Step]
    fulfilled_steps:list[Step]
    research_evidence_verified:Literal[False]=False

    @model_validator(mode='after')
    def consistent_completion(self):
        required=set(self.required_steps);fulfilled=set(self.fulfilled_steps)
        base={'source_selection','common_target_query'}
        if (len(required)!=len(self.required_steps) or len(fulfilled)!=len(self.fulfilled_steps)
                or required != base | (set() if self.outcome=='empty' else {'selected_evidence_check'})
                or (self.status=='complete')!=(required<=fulfilled)
                or (self.outcome in ('matched','empty'))!=(self.status=='complete')
                or (self.outcome=='empty' and 'selected_evidence_check' in fulfilled)
                or (self.outcome=='source_unresolved' and 'source_selection' in fulfilled)):
            raise ValueError('Inconsistent local completion contract')
        return self


def local_relation_completion(task):
    sources=task['sources'];records=task.get('selected_source_records',[])
    selected=(len(sources)==2 and len(set(sources))==2 and len(records)==2 and
              {r['source_record_id'] for r in records}==set(sources))
    queried=selected and any(e.get('kind')=='tool' and e.get('name')=='common_targets' and
        type(e.get('observation',{}).get('total')) is int and
        e['observation']['total']==task.get('common_target_total') for e in task['events'])
    empty=queried and task['common_target_total']==0
    targets=task['targets'];by_id={r['id']:r for r in task['relations']}
    evidence=(queried and task['common_target_total']>0 and 1<=len(targets)<=2 and
              len(set(targets))==len(targets) and set(targets)<=set(by_id))
    if evidence:
        for target in targets:
            witnesses=by_id[target]['witnesses']
            evidence &= ({w['source_id'] for w in witnesses}==set(sources) and all(
                isinstance(w.get('json_pointer'),str) and w['json_pointer'].startswith('/') and
                isinstance(w.get('raw_row'),dict) and bool(w['raw_row']) for w in witnesses))
    required=['source_selection','common_target_query']+([] if empty else ['selected_evidence_check'])
    fulfilled=(['source_selection'] if selected else [])+(['common_target_query'] if queried else [])+(['selected_evidence_check'] if evidence else [])
    done=set(required)<=set(fulfilled)
    outcome='empty' if empty else 'matched' if done else 'source_unresolved' if not selected else 'incomplete'
    return TaskCompletion(status='complete' if done else 'partial',outcome=outcome,
                          required_steps=required,fulfilled_steps=fulfilled)
