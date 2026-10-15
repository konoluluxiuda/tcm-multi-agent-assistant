"""Execute the two frozen planned-chat candidates through the real main /chat route.

Checks the originally recorded future targets; leaves all candidate metadata unchanged.
Runs in a sanitized subprocess with model calls and outbound network forbidden.
"""
import argparse
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from unittest.mock import patch

from scripts.run_offline_ci import ROOT, test_environment
from scripts.eval_tcm_golden import database_preflight
from scripts.validate_tcm_task_candidates import validate

SAME = 'TCM-CAND-MULTI_STEP-04'
ISOLATED = 'TCM-CAND-MULTI_STEP-05'


def check_same_session(case, turns):
    target = case['expected']['future_target']
    evidence = case['source_evidence'][0]
    for turn in turns:
        body = turn['body']
        assert turn['http_status'] == 200 and body['agent'] == 'TCM_HERB_AGENT'
        result = body['tcm']
        assert result['status'] == 'ok' and len(result['records']) == 1
        assert result['dataset_version'] == case['input']['dataset_version']
        record = result['records'][0]
        assert record['source_record_id'] == target['source_record_id']
        for key, expected in target['fields'].items():
            field = record['fields'][key]
            assert field['state'] == expected['state'] and field['value'] == expected['value']
            source = field['source']
            assert source['relative_file_path'] == evidence['relative_path']
            assert source['file_sha256'] == evidence['sha256']
            assert source['json_pointer'] == evidence['json_pointer']
            assert source['raw_value'] == evidence['raw_value']
    assert len(turns) == 2 and turns[1]['body']['tcm']['followup'] is True


def check_isolated_session(case, turns):
    assert len(turns) == 2
    forbidden = case['expected']['future_target']['forbidden_implicit_source_record_id']
    setup = turns[0]
    assert setup['http_status'] == 200 and setup['body']['agent'] == 'TCM_HERB_AGENT'
    assert setup['body']['tcm']['status'] == 'ok'
    assert setup['body']['tcm']['dataset_version'] == case['input']['dataset_version']
    assert len(setup['body']['tcm']['records']) == 1
    record = setup['body']['tcm']['records'][0]
    assert record['source_record_id'] == forbidden and 'family' in record['fields']
    source = record['fields']['name_pinyin']['source']
    evidence = case['source_evidence'][0]
    for key, expected in [('relative_file_path', evidence['relative_path']),
                          ('file_sha256', evidence['sha256']),
                          ('json_pointer', evidence['json_pointer']), ('raw_value', evidence['raw_value'])]:
        assert source[key] == expected
    body = turns[1]['body']
    assert turns[1]['http_status'] == 200 and body['agent'] == 'TCM_HERB_AGENT'
    assert body['tcm']['status'] == 'clarify_entity' and body['tcm']['records'] == []
    assert '/herb' in body['response']
    assert forbidden not in json.dumps(body)


def run_worker(suite_path, database, output):
    raw = suite_path.read_bytes()
    suite = json.loads(raw)
    errors = validate(suite)['errors']
    if errors: raise ValueError('; '.join(errors))
    database_preflight(database, {'dataset_version': suite['dataset_version'], 'snapshot': suite['source_snapshot']})
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    cases = {c['id']: c for c in suite['cases'] if c['id'] in (SAME, ISOLATED)}
    assert len(cases) == 2
    attempts, model_calls = [], []
    def deny_network(*args, **kwargs):
        attempts.append('blocked')
        raise RuntimeError('Network forbidden in chat candidate evaluation')
    def forbid_model(*args, **kwargs):
        model_calls.append('blocked')
        raise RuntimeError('Model forbidden in chat candidate evaluation')
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'method': 'main FastAPI /chat via in-process ASGI; distinct cookie jars for distinct sessions',
              'suite_sha256': hashlib.sha256(raw).hexdigest(), 'database_sha256': before,
              'human_signoff': False, 'task_completion_rate': None,
              'interpretation': 'Machine checks of previously planned future targets; frozen capability_status is historical and unchanged.',
              'cases': [], 'source_code_sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                 for name in ['tcm/chat.py', 'agents/agent_decision.py', 'app.py', 'scripts/eval_tcm_chat_tasks.py']}}
    import dotenv
    with ExitStack() as stack:
        stack.enter_context(patch.object(dotenv, 'load_dotenv', return_value=False))
        for obj, name in [(socket.socket, 'connect'), (socket.socket, 'connect_ex'),
                          (socket.socket, 'sendto'), (socket, 'getaddrinfo')]:
            stack.enter_context(patch.object(obj, name, deny_network))
        stack.enter_context(patch.dict(os.environ, {'TCM_DB': str(database.resolve()),
                                                   'TCM_DATASET_VERSION': suite['dataset_version']}))
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        from langgraph.checkpoint.memory import MemorySaver
        from agents import agent_decision as ad
        import app
        stack.enter_context(patch.object(ad, 'memory', MemorySaver()))
        for target in [ad.config.agent_decision, ad.config.conversation]:
            stack.enter_context(patch.object(target, 'llm', RunnableLambda(forbid_model)))
        stack.enter_context(patch.object(ad.LocalGuardrails, 'check_input', forbid_model))
        stack.enter_context(patch.object(ad.LocalGuardrails, 'check_output', forbid_model))
        for case_id in (SAME, ISOLATED):
            case = cases[case_id]
            result = {'id': case_id, 'frozen_capability_status': case['capability_status'],
                      'target_checked': 'future_target', 'turns': []}
            try:
                with ExitStack() as clients:
                    sessions = ({'A': case['input']['turns']} if case_id == SAME else case['input']['sessions'])
                    for label, queries in sessions.items():
                        client = clients.enter_context(TestClient(app.app))
                        for query in queries:
                            started = time.perf_counter()
                            with redirect_stdout(io.StringIO()):
                                response = client.post('/chat', json={'query': query})
                            result['turns'].append({'session': label, 'query': query,
                                'latency_seconds': round(time.perf_counter() - started, 6),
                                'http_status': response.status_code, 'body': response.json()})
                (check_same_session if case_id == SAME else check_isolated_session)(case, result['turns'])
                result['machine_status'] = 'passed'
            except Exception as exc:
                result.update(machine_status='failed', error=f'{type(exc).__name__}: {exc}')
            report['cases'].append(result)
    report.update(network_attempts=len(attempts), model_attempts=len(model_calls),
                  sqlite_unchanged=hashlib.sha256(database.read_bytes()).hexdigest() == before,
                  suite_unchanged=suite_path.read_bytes() == raw)
    success = (all(c['machine_status'] == 'passed' for c in report['cases']) and
               not attempts and not model_calls and report['sqlite_unchanged'] and report['suite_unchanged'])
    report['status'] = 'passed' if success else 'failed'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'cases': len(report['cases']), 'task_completion_rate': None}))
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT / 'eval/tcm_task_candidates_v1.json')
    parser.add_argument('--db', type=Path, default=ROOT / 'var/tcm/herbs.sqlite3')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/task_candidates/chat.json')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.out.resolve() in (args.suite.resolve(), args.db.resolve()):
        parser.error('output must not overwrite inputs')
    if args.worker: return run_worker(args.suite, args.db, args.out)
    return subprocess.run([sys.executable, '-m', 'scripts.eval_tcm_chat_tasks', '--worker',
                           '--suite', str(args.suite.resolve()), '--db', str(args.db.resolve()),
                           '--out', str(args.out.resolve())], cwd=ROOT, env=test_environment()).returncode


if __name__ == '__main__':
    sys.exit(main())
