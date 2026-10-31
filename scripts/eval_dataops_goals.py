"""Preview-first G experiment: bounded grammar vs one model Goal, same fixed executor.

Never enable a model in the HTTP service. Known author-written synthetic dev
cases only; reserve provider dispatch before sending and never replay a run.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'eval/dataops_goal_understanding_v1.json'


def preview():
    return {'version': 'dataops-goal-live-v1', 'status': 'prepared_not_run',
        'suite_sha256': hashlib.sha256(SUITE.read_bytes()).hexdigest(),
        'case_ids': [c['case_id'] for c in json.loads(SUITE.read_text())['cases']],
        'strategies': {'R': 'existing local bounded grammar', 'G': 'one native Goal proposal'},
        'executor': 'same FixedPlanDriver and graph/tools/source oracle for accepted goals',
        'endpoint': 'https://api.deepseek.com/chat/completions', 'model': 'deepseek-flash',
        'max_requests_total': 12, 'max_requests_per_case': 1, 'max_output_tokens': 1200,
        'max_request_bytes': 65536, 'request_timeout_seconds': 30, 'retries': 0,
        'response_corrections': 0, 'thinking': {'type': 'disabled'}, 'parallel_tool_calls': False,
        'data': 'Current fixed synthetic question, bounded synthetic alias catalog, allowed schema/defaults and parent field summary only.',
        'excluded': ['reference answers', 'real ETCM', 'pharmacopoeia', 'PDF', 'history', 'credentials in prompts'],
        'stop_on': 'Unknown dispatch or provider HTTP error: stop remaining model cases, never resend.',
        'scope': 'author-written known development; safe deferral is not a proven high-quality clarification',
        'real_model_calls': 0, 'http_integration': False}


def goal_signature(goal, catalog):
    from tcm.dataops.contracts import Goal
    g = Goal.model_validate(goal)
    aliases = {k.casefold(): v for k,v in catalog.items()}
    ids = set(g.query.source_ids)
    for name in g.query.names:
        if name.casefold() not in aliases:
            raise ValueError('Unknown reference name')
        ids.update(aliases[name.casefold()])
    return {'source_ids': sorted(ids), 'filters': sorted(
        ((f.field, f.op, f.value) for f in g.query.filters)),
        'limit': g.query.limit, 'offset': g.query.offset, 'group_by': g.query.group_by,
        'attributes': sorted(g.attributes), 'document_fields': sorted(g.document_fields),
        'completion_policy': g.completion_policy}


def score(case, proposal, catalog):
    errors = []
    if proposal.get('status') != case['expected_status']:
        errors.append('wrong_ready_or_deferral_status')
    unsafe = case['expected_status'] == 'needs_input' and proposal.get('status') == 'ready'
    if case['expected_status'] == 'ready' and proposal.get('status') == 'ready':
        expected = goal_signature(case['expected_goal'], catalog)
        actual = goal_signature(proposal['goal'], catalog)
        errors.extend('goal_mismatch:' + key for key in expected if actual[key] != expected[key])
        if proposal.get('refresh_source_ids'):
            errors.append('unsolicited_refresh')
    return {'passed': not errors, 'errors': errors, 'unsafe_acceptance': unsafe,
            'clear_request_needs_rephrasing': case['expected_status'] == 'ready' and proposal.get('status') != 'ready'}


def budget_transport(out):
    import httpx
    from tcm.dataops.evaluation import write
    spec = preview()
    class Transport(httpx.BaseTransport):
        def __init__(self):
            self.inner = httpx.HTTPTransport(retries=0)
            self.requests = []
            self.case_id = None
            self.stopped = False

        def handle_request(self, request):
            body = request.read(); url = urlsplit(str(request.url))
            if (self.stopped or self.case_id not in spec['case_ids'] or
                any(r['case_id'] == self.case_id for r in self.requests) or
                len(self.requests) >= spec['max_requests_total'] or len(body) > spec['max_request_bytes'] or
                request.method != 'POST' or url.scheme != 'https' or url.hostname != 'api.deepseek.com' or
                url.port not in (None,443) or url.path != '/chat/completions'):
                raise RuntimeError('Goal provider recipient/budget rejected')
            data = json.loads(body)
            if (data.get('model') != spec['model'] or data.get('thinking') != spec['thinking'] or
                data.get('parallel_tool_calls') is not False or data.get('tool_choice') != 'required' or
                type(data.get('max_tokens')) is not int or not 1 <= data['max_tokens'] <= 1200 or
                'max_completion_tokens' in data or len(data.get('tools',[])) != 1 or
                data['tools'][0].get('function',{}).get('name') != 'propose_dataops_goal'):
                raise RuntimeError('Goal provider payload rejected')
            receipt = {'sequence':len(self.requests)+1, 'case_id':self.case_id,
                       'bytes':len(body), 'outcome':'unknown'}
            self.requests.append(receipt)
            write(out/'dispatch.json', self.requests)
            self.stopped = True  # Unknown network outcome must stop the experiment.
            response = self.inner.handle_request(request)
            receipt['outcome'] = 'http_' + str(response.status_code)
            if response.status_code == 200:
                payload = json.loads(response.read())
                message = (payload.get('choices') or [{}])[0].get('message', {})
                calls = message.get('tool_calls') or []
                usage = payload.get('usage') or {}
                receipt['reply_summary'] = {'tool_call_count':len(calls), 'tool_names':[
                    'propose_dataops_goal' if c.get('function',{}).get('name') == 'propose_dataops_goal'
                    else 'unregistered' for c in calls[:4]], 'usage':{k:usage[k]
                    for k in ('prompt_tokens','completion_tokens','total_tokens')
                    if type(usage.get(k)) is int and usage[k] >= 0}}
                self.stopped = False
            write(out/'dispatch.json', self.requests)
            return response

        def close(self):
            self.inner.close()
    return Transport()


def evaluate(out, model=None, transport=None):
    """No implicit model construction; tests inject a fake, production CLI opts in."""
    import time
    from tests.dataops_fixture import make_sources
    from tcm.dataops.baseline import execute as fixed_parent
    from tcm.dataops.contracts import Goal
    from tcm.dataops.data_access import fingerprint
    from tcm.dataops.evaluation import SourceOracle, run_case, write
    from tcm.dataops.goal_model import request_proposal
    from tcm.dataops.goal_proposal import propose
    from tcm.dataops.workspace import Workspaces
    from tcm.tracing import observation, flush

    if out.exists():
        raise FileExistsError('Never replay or overwrite an experiment')
    out.mkdir(parents=True)
    suite = json.loads(SUITE.read_text())
    spec = preview()
    write(out/'protocol.json', spec)
    write(out/'suite.json', suite)
    _, factory = make_sources(out/'fixture')
    oracle = SourceOracle(out/'fixture')
    with factory() as sources:
        catalog = sources.name_catalog()
    references = {}
    parents = {}
    for case in suite['cases']:
        if case['expected_goal']:
            ref_case = {**case, 'category':'delivery', 'goal':case['expected_goal'], 'expected_status':'complete'}
            references[case['case_id']] = oracle.reference(ref_case)
        if case['parent_goal']:
            with factory() as sources:
                parent, _ = fixed_parent(Workspaces(out/'parents'/case['case_id']), 'benchmark-owner',
                    'parent-goal', sources, Goal.model_validate(case['parent_goal']).model_dump())
            parents[case['case_id']] = parent
    write(out/'reference.json', references)
    write(out/'catalog.json', catalog)
    code = [*list((ROOT/'tcm/dataops').glob('*.py')), Path(__file__).resolve(),
            ROOT/'scripts/eval_dataops_live.py',ROOT/'tests/dataops_fixture.py', ROOT/'tcm/tracing.py']
    write(out/'source_code.json', {str(p.relative_to(ROOT)):fingerprint(p) for p in code})
    results = []
    for case in suite['cases']:
        for strategy in ('R','G'):
            started = time.perf_counter()
            before = len(transport.requests) if transport else 0
            if strategy == 'G' and (model is None or transport and transport.stopped):
                row = {'case_id':case['case_id'], 'category':case['category'], 'strategy':strategy,
                       'status':'not_run', 'reason':'no_model_or_provider_stop', 'actual_provider_requests':0}
            else:
                with observation('dataops.goal_evaluation', input={'question':case['question']},
                     metadata={'experiment_id':out.name,'case_id':case['case_id'], 'strategy':strategy,
                               'protocol':spec['version']}) as trace:
                    parent = parents.get(case['case_id'])
                    error_type = None
                    try:
                        if strategy == 'R':
                            proposal = propose(case['question'], catalog, parent=parent)
                        else:
                            if transport: transport.case_id = case['case_id']
                            proposal = request_proposal(model, case['question'], catalog, parent=parent, authorized=True)
                    except Exception as exc:
                        # Never turn a timeout or malformed response into a correct clarification.
                        error_type = type(exc).__name__
                        proposal = {'status':'proposal_error','goal':None,'issues':[], 'unresolved':[]}
                    checked = score(case, proposal, catalog)
                    delivery = None
                    if proposal['status'] == 'ready' and case['expected_status'] == 'ready':
                        execution_case = {**case, 'category':'delivery', 'expected_status':'complete',
                                          'goal':proposal['goal']}
                        delivery = run_case(deepcopy(execution_case), references[case['case_id']], oracle,
                                            'F', factory, out/'runs'/case['case_id']/strategy)
                        if delivery['status'] != 'passed':
                            checked['errors'].append('fixed_delivery_failed')
                            checked['passed'] = False
                    row = {'case_id':case['case_id'], 'category':case['category'], 'strategy':strategy,
                           'status':'passed' if checked['passed'] else 'failed', **checked,
                           'proposal':proposal, 'error_type':error_type, 'fixed_delivery':delivery,
                           'unsafe_execution_blocked_by_evaluator':checked['unsafe_acceptance'],
                           'actual_provider_requests':len(transport.requests)-before if transport else 0,
                           'elapsed_seconds':time.perf_counter()-started, 'cost':None}
                    trace.update(output=row, metadata={'actual_provider_requests':row['actual_provider_requests']},
                                 level='DEFAULT' if row['status']=='passed' else 'WARNING')
                    trace.score('development_goal_contract_pass',int(row['status']=='passed'))
            results.append(row)
            write(out/'progress.json',results)
    report = {'kind':spec['scope'], 'status':'completed', 'results':results,
              'actual_provider_requests':len(transport.requests) if transport else 0,
              'model_strategy_executed':model is not None, 'http_integration':False,
              'real_data_or_history_sent':False, 'holdout_or_business_accuracy_claim':False, 'cost':None}
    write(out/'summary.json',report)
    flush()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-synthetic', action='store_true')
    parser.add_argument('--local-only', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.execute_synthetic and args.local_only:
        parser.error('Choose one execution mode')
    if not (args.execute_synthetic or args.local_only):
        print(json.dumps(preview(),ensure_ascii=False,indent=2)); return 0
    if args.out is None or args.out.exists():
        parser.error('Use --out with a new directory')
    os.environ.update(LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false')
    if args.local_only:
        report = evaluate(args.out)
    else:
        import httpx
        from dotenv import dotenv_values
        from scripts.eval_dataops_live import configured_model
        key = os.environ.get('CHAT_API_KEY') or dotenv_values(ROOT/'.env').get('CHAT_API_KEY')
        if not key: raise ValueError('CHAT_API_KEY is missing')
        os.environ['CHAT_API_KEY'] = key  # Make credential available to the trace redactor.
        transport = budget_transport(args.out)
        with httpx.Client(transport=transport, timeout=30, follow_redirects=False, trust_env=False) as http:
            report = evaluate(args.out, configured_model(key,http), transport)
    print(json.dumps({'status':report['status'], 'actual_provider_requests':report['actual_provider_requests'],
                      'passed':{s:sum(r['strategy']==s and r['status']=='passed' for r in report['results'])
                                for s in ('R','G')}},ensure_ascii=False))
    return 1 if any(r['strategy']=='G' and r['status']!='passed' for r in report['results']) and args.execute_synthetic else 0


if __name__ == '__main__':
    raise SystemExit(main())
