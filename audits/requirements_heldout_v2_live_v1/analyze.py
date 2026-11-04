"""Offline analysis of immutable first-run receipts; never invokes providers."""
import json
import sqlite3
from pathlib import Path
from pydantic import ValidationError
from tcm.dataops.requirements_v2 import RequirementsDraftV2
from scripts.eval_requirements_generalization import _save

root=Path('var/requirements_heldout_v2/live_v1')
report=json.loads((root/'reports/0001.json').read_text())
with sqlite3.connect((root/'ledger.sqlite3').resolve().as_uri()+'?mode=ro',uri=True) as db:
    rows=db.execute('SELECT id,policy,result FROM cases ORDER BY rowid').fetchall()
    dispatches=db.execute('SELECT id,scenario,policy,turn,status,http,usage FROM dispatches ORDER BY id').fetchall()
checks=[]
for sid,policy,raw in rows:
    result=json.loads(raw);folder=root/'cases'/f'{sid}_{policy}'
    item={'scenario':sid,'policy':policy,'terminal_state':result['steps'][-1]['terminal_state'],'schema_failures':[]}
    for file in sorted(folder.glob('response-*.private.json')):
        body=json.loads(file.read_text())['body']
        for choice in body.get('choices',[]):
            for call in choice.get('message',{}).get('tool_calls',[]):
                if call['function']['name']!='submit_requirements_v2':continue
                args=json.loads(call['function']['arguments'])
                try:RequirementsDraftV2.model_validate(args)
                except ValidationError as exc:
                    item['schema_failures'].append({'receipt':str(file),'errors':[{'path':e['loc'],'message':e['msg']} for e in exc.errors()], 'delivery':args.get('delivery')})
    checks.append(item)
summary={'status':'stopped_as_authorized_after_unknown_dispatch','manifest_sha256':json.loads((root/'preflight.json').read_text())['manifest_sha256'],
 'planned_scenarios':40,'planned_combinations':120,'started_combinations':len(rows),'not_started_combinations':120-len(rows),
 'requests':len(dispatches),'received_http_200':sum(d[5]==200 for d in dispatches),'unknown_requests':sum(d[4]=='unknown' for d in dispatches),
 'known_total_tokens':report['known_total_tokens'],'unknown_usage_not_zero':True,'automatic_retries':0,
 'raw_grader_passes_on_started':{p:sum(s['completed'] for s in report['scores'] if s['policy']==p and s['run_status']!='not_run') for p in ['R','G','V']},
 'scoring_caveat':'HV2-02 does not explicitly specify dataset; raw gold only accepts ETCM. Keep raw score, do not interpret pharmacy evidence as necessarily incorrect.',
 'proven_g_benefit_cases':['HV2-01','HV2-03','HV2-04'],'global_generalization_proven':False,
 'schema_replay':checks,'production_code_changed':False,'default_policy_changed':False,'new_provider_calls_during_analysis':0}
_save('audits/requirements_heldout_v2_live_v1/findings.json',summary)
print(json.dumps({k:v for k,v in summary.items() if k!='schema_replay'},ensure_ascii=False))
