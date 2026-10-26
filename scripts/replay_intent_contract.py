"""Replay saved synthetic tool arguments through current validation; never call a model."""
import argparse
import hashlib
import json
from pathlib import Path
from tcm.intent_planner import QueryIntent, mapped
from scripts.eval_intent_contract_live import score

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    suite_path=ROOT/'eval/intent_contract_synthetic_v1.json'
    suite=json.loads(suite_path.read_text())
    origin=ROOT/'audits/intent_contract_synthetic_v1/live'
    manifest=json.loads((origin/'manifest.json').read_text())
    assert hashlib.sha256(suite_path.read_bytes()).hexdigest()==manifest['question_sha256']
    rows=[]
    for case in suite['cases']:
        raw_path=origin/(case['id']+'.json');previous=json.loads(raw_path.read_text())
        proposal=previous['proposal']
        try:
            intent=QueryIntent.model_validate(proposal)
            route={**mapped(intent,case['question']),'intent_status':'ok'}
            normalized=intent.model_dump()
        except ValueError:
            route={'tool':'assistant','intent_status':'invalid_output'};normalized=None
        row={'id':case['id'],'raw_sha256':hashlib.sha256(raw_path.read_bytes()).hexdigest(),
             'original_passed':previous['passed'],'original_proposal':proposal,
             'normalized':normalized,'route':route,**score(case,route,proposal)}
        rows.append(row)
    report={'mode':'saved_proposal_replay_not_live_model_quality','external_calls':0,'total':len(rows),
            'passed':sum(r['passed'] for r in rows),'cases':rows}
    (args.out/'replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('mode','external_calls','total','passed')}))


if __name__=='__main__':main()
