"""Freeze and run offline F/P/A business contracts; never construct an external model."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import socket
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def run(args):
    # Source text/history must not leave this process, including via tracing.
    os.environ.update(LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
    from tests.dataops_fixture import make_sources
    from tcm.dataops.data_access import fingerprint
    from tcm.dataops.evaluation import SourceOracle, run_case, write
    if args.out.exists():
        raise FileExistsError('Use a fresh output directory; first results must not be overwritten')
    suite = json.loads(args.suite.read_text())
    cases = [c for c in suite['cases'] if args.split=='all' or c['split']==args.split]
    if len(suite['cases']) != 30 or Counter(c['split'] for c in suite['cases']) != {'development':18,'prospective_holdout':12}:
        raise ValueError('Unexpected suite size/split')
    args.out.mkdir(parents=True)
    _, factory = make_sources(args.out / 'fixture')
    oracle = SourceOracle(args.out / 'fixture')
    references = {case['case_id']: oracle.reference(case) for case in cases}
    with factory() as source:
        revision = source.revision
    code = {str(p.relative_to(ROOT)): fingerprint(p) for p in (ROOT/'tcm/dataops').glob('*.py')}
    code.update({str(p.relative_to(ROOT)):fingerprint(p) for p in (ROOT/'scripts/eval_dataops.py',ROOT/'tests/dataops_fixture.py')})
    protocol = dict(version='dataops-offline-evaluation-v1',suite_sha256=fingerprint(args.suite), source_revision=revision,
        code=code, split=args.split, strategies=args.strategies, fixture='synthetic_four_records',
        scope=suite['scope'], cases=[c['case_id'] for c in cases],
        interpretation='F fixed host; P frozen scripted plan; A synthetic observation policy. No real LLM effectiveness comparison.',
        external_model_calls=0, reviewed_by='author', independent_user_evaluation=False,
        real_model_comparison='not_run', cost=None, frozen_before_execution=True)
    write(args.out/'protocol.json',protocol);write(args.out/'reference.json',references)
    attempts=[]
    def forbidden(*a, **kw):
        attempts.append('external_socket_attempt');raise AssertionError('Offline evaluation forbids all network')
    results=[];started=time.perf_counter()
    with patch.object(socket.socket,'connect',forbidden),patch.object(socket,'getaddrinfo',forbidden):
        for case in cases:
            for strategy in args.strategies:
                directory=args.out/'runs'/case['case_id']/strategy
                result=run_case(case,references[case['case_id']],oracle,strategy,factory,directory)
                result.update(case_id=case['case_id'],split=case['split'],strategy=strategy,category=case['category'])
                directory.mkdir(parents=True,exist_ok=True);write(directory/'evaluation.json',result)
                results.append(result)
            print(case['case_id']+': '+', '.join(r['strategy']+'='+r['status'] for r in results[-len(args.strategies):]),flush=True)
    assert all(fingerprint(ROOT/name)==sha for name,sha in code.items()), 'Code changed while the suite was running'
    with factory() as source:
        assert source.revision==revision,'Source fixture changed'
    summary={'status':'passed' if not attempts and all(r['status']=='passed' for r in results) else 'failed',
        'cases':len(cases),'strategy_runs':len(results),'split':args.split,
        'suite_sha256':protocol['suite_sha256'],'protocol_sha256':fingerprint(args.out/'protocol.json'),
        'reference_sha256':fingerprint(args.out/'reference.json'),
        'strategies':{s:{'executed':sum(r['strategy']==s for r in results),
            'contract_passed':sum(r['strategy']==s and r['status']=='passed' for r in results),
            'outcomes':dict(Counter(r['outcome'] for r in results if r['strategy']==s)),
            'external_model_calls':0,'provider_usage':None,'cost':None} for s in args.strategies},
        'results':results,'network_attempts':len(attempts),'external_model_calls':0,
        'source_unchanged':True,'elapsed_seconds':time.perf_counter()-started,
        'real_model_comparison':'not_run','natural_language_goal_compilation':'not_evaluated',
        'holdout_note':'Author-designed prospective combinations. Once executed they are regression cases, not unseen holdout.',
        'no_llm_improvement_claim':True}
    write(args.out/'summary.json',summary)
    print(json.dumps({k:summary[k] for k in ('status','cases','strategy_runs','split','network_attempts')},ensure_ascii=False))
    return 0 if summary['status']=='passed' else 1


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',type=Path,default=ROOT/'eval/dataops_tasks_v1.json')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--split',choices=['development','prospective_holdout','all'],default='development')
    p.add_argument('--strategies',nargs='+',choices=['F','P','A'],default=['F','P','A'])
    args=p.parse_args()
    if len(set(args.strategies))!=len(args.strategies):p.error('Duplicate strategy')
    return run(args)


if __name__=='__main__':raise SystemExit(main())
