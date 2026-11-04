"""Read-only review probes; temporary run dirs and fake client, no real API/model."""
import json
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch
from scripts import eval_requirements_generalization as runner
from tcm.evaluation.requirements_benchmark import Scenario, SourceAssertion, score


def main():
    findings = []
    def record(name, observed, **details):
        findings.append({'probe': name, 'defect_observed': bool(observed), **details})
    def scenario():
        return Scenario.model_validate({'id':'X01', 'category':'multi_field',
            'turns':[{'id':'t1','question':'Alpha和Beta的科名'}],
            'expected':{'task_class':'supported','objects':['Alpha','Beta'],
                'requirements':[{'object_ref':r,'namespace':'etcm','name':'family'} for r in ('o1','o2')],
                'delivery':'answer','allowed_terminal_states':['success']}})
    s=scenario(); result=runner._reference_result(s)
    result.steps[0].fields=[{'namespace':'etcm','name':'family'}]
    record('unbound_single_field_satisfies_two_objects',score(s,result).completed)
    s=scenario();s.expected.missing_assertions=['注意未记录']
    record('missing_assertions_ignored',score(s,runner._reference_result(s)).completed)
    s=scenario();s.expected.source_assertions=[SourceAssertion(object_ref='o1',namespace='etcm',name='family',source_document='A.json',value_contains='CORRECT')]
    result=runner._reference_result(s)
    result.steps[0].sources=[{'object_ref':'o1','source_document':'A.json','namespace':'pharmacopoeia','name':'wrong','value':'WRONG'}]
    record('wrong_source_value_namespace_field_passes',score(s,result).completed)
    raw=scenario().model_dump();raw['category']='table';raw['turns'].append({'id':'t2','question':'增加药用部位'})
    raw['expected'].update(delivery='table',table={'row_policy':'top_n','count':2,'parent_fields':['family']})
    s=Scenario.model_validate(raw);result=runner._reference_result(s);result.artifact_sha256='not-a-hash'
    record('missing_second_turn_and_nonexistent_artifact_pass',score(s,result).completed, observed_turns=[x.turn_id for x in result.steps])
    result.steps[0].http_status=503
    record('http503_with_claimed_success_passes',score(s,result).completed)
    draft=[Scenario.model_validate(json.loads(line)) for line in Path('eval/requirements_generalization_v1/questions.private.draft.jsonl').read_text().splitlines() if line.strip()]
    turns=sum(len(x.turns) for x in draft)
    record('multi_turn_budget_undercounted',runner._budget(draft)['total']!=3*turns,scenarios=len(draft),turns=turns,reported=runner._budget(draft),correct_maximum={'R':0,'G':turns,'V':2*turns,'total':3*turns})
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);suite=tmp/'suite.json';suite.write_text(json.dumps([scenario().model_dump()]))
        class FakeClient:
            def __init__(self):self.calls=0
            def run(self,s,p):
                self.calls+=1;r=runner._reference_result(s);r.policy=p;return r
        out=tmp/'run';runner.prepare(out,suite)
        try:runner.main(['execute','--out',str(out),'--authorize-execute'])
        except ValueError as exc:record('cli_execute_has_no_client','HTTP client' in str(exc),error=str(exc))
        frozen=json.loads((out/'questions.private.jsonl').read_text());frozen[0]['turns'][0]['question']='altered after freeze'
        (out/'questions.private.jsonl').write_text(json.dumps(frozen))
        c=FakeClient();runner.execute(out,authorize=True,client=c)
        record('altered_frozen_question_executes',c.calls==3)
        with patch.object(runner,'_code_hashes',return_value={'runner':'changed-script'}):
            before=c.calls;runner.execute(out,authorize=True,client=c)
            record('execute_does_not_validate_script_hash',c.calls-before==3)
        before=c.calls;runner.execute(out,authorize=True,client=c)
        record('repeat_execute_repeats_combinations',c.calls-before==3)
        only=runner._reference_result(scenario());only.policy='R';runner._save(out/'results.private.jsonl',[only.model_dump()])
        report=runner.score_run(out)
        record('missing_policy_combinations_not_in_denominator',report=={'R':{'completed':1,'total':1}},reported=report)
        crash=tmp/'crash';runner.prepare(crash,suite)
        class CrashClient(FakeClient):
            def run(self,s,p):
                if self.calls:raise TimeoutError('synthetic timeout, no request sent')
                return super().run(s,p)
        c=CrashClient()
        try:runner.execute(crash,authorize=True,client=c)
        except TimeoutError:pass
        record('interruption_loses_completed_first_result',c.calls==1 and not (crash/'results.private.jsonl').exists())
    print(json.dumps({'mode':'offline-fake-client','real_model_requests':0,'findings':findings},ensure_ascii=False,indent=2))
    assert all(f['defect_observed'] for f in findings)


if __name__=='__main__':
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')), patch.object(socket,'getaddrinfo',side_effect=AssertionError('Network forbidden')):
        main()
