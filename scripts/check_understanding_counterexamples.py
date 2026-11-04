"""Offline proposal gate; checks stages/codes and fails on mismatches or errors.

Table cases exercise the compiler; query cases exercise the real one-response
query validation path with an SDK substitute. This is not model accuracy or
end-to-end business acceptance. The original v1 fixture remains unchanged.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pydantic import ValidationError
from tcm.dataops.research_question import compile_draft, request_proposal
from tcm.dataops.diagnostics import ContractDiagnosticError
from tcm.dataops.goal_model import GoalResponseRejected

ROOT = Path(__file__).resolve().parents[1]
ROW_POLICY_TO_MODE = {'keep_all': 'audit_named', 'filter_complete': 'filter_complete', 'top_n': 'select_complete'}
HERB_IDS = {name: 'etcm2-local:' + pair * 32 for name, pair in
            zip(('白术', '黄芪', '当归', '川芎', '甘草', '大黄'), ('a1', 'b2', 'c3', 'd4', 'e5', 'f6'))}


def build_catalog(question):
    return {name: [sid] for name, sid in HERB_IDS.items() if name in question}


def build_draft(case):
    p = case['proposal']; attributes, document_fields = [], []
    for field in p.get('fields', []):
        namespace, separator, name = field.partition(':')
        if not separator or namespace not in ('etcm', 'pharmacopoeia'):
            raise ValueError('Invalid fixture field namespace')
        (attributes if namespace == 'etcm' else document_fields).append(name)
    return {'status': 'ready', 'operation': p.get('operation', 'new'),
            'mode': ROW_POLICY_TO_MODE[p['row_policy']], 'names': list(p.get('objects', [])),
            'filters': p.get('filters', []), 'target_count': p.get('count'),
            'attributes': attributes, 'document_fields': document_fields,
            'monograph_only': p.get('monograph_only'), 'issues': [], 'unresolved': []}


class FixtureModel:
    max_retries = 0
    max_tokens = 1200
    model_name = 'offline-counterexample-fixture'

    def __init__(self, intent):
        self.intent = intent
        self.calls = 0

    def bind_tools(self, *args, **kwargs):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage
        self.calls += 1
        if self.calls > 1:
            raise AssertionError('Gate permits one substitute response only')
        return AIMessage(content='', tool_calls=[{'id': 'fixture', 'name': 'submit_query_intent', 'args': self.intent}])


def run_case(case):
    model = None
    try:
        catalog = build_catalog(case['question'])
        if case['proposal']['output'] == 'table':
            value = compile_draft(build_draft(case), case['question'], catalog, parent=case.get('parent_fixture'))
            actual = {'outcome': 'accept', 'stage': 'goal_validation'} if value['status'] == 'ready' else {
                'outcome': 'reject', 'stage': value.get('failure', {}).get('stage', 'requirements'),
                'code': value.get('failure', {}).get('code')}
        elif case['proposal']['output'] == 'answer':
            model = FixtureModel(case['query_intent'])
            value = request_proposal(model, case['question'], catalog, authorized=True, allow_queries=True,
                                     policy='local_then_one_model_v1')
            actual = {'outcome': 'accept', 'stage': 'query_mapping'} if value['status'] == 'query' else {
                'outcome': 'reject', 'stage': value.get('failure', {}).get('stage', 'requirements'),
                'code': value.get('failure', {}).get('code')}
        else:
            raise ValueError('Unknown fixture output form')
    except ContractDiagnosticError as exc:
        actual = {'outcome': 'reject', 'stage': 'goal_validation', 'code': exc.code}
    except ValidationError:
        actual = {'outcome': 'reject', 'stage': 'draft_schema', 'code': 'schema_validation_failed'}
    except GoalResponseRejected as exc:
        rejection = exc.diagnostics.get('rejection', {})
        actual = {'outcome': 'reject', 'stage': rejection.get('validation_stage', 'proposal_validation'),
                  'code': rejection.get('code')}
    except Exception as exc:
        # A programmer/fixture error is never a successful semantic rejection.
        actual = {'outcome': 'error', 'stage': 'harness', 'exception_type': type(exc).__name__}
    matched = actual['outcome'] == case['expect'] and actual['stage'] == case.get('expected_stage')
    if case['expect'] == 'reject':
        matched = matched and bool(case.get('reject_code')) and actual.get('code') == case['reject_code']
    return {'id': case['id'], 'matched': matched, 'expected': case['expect'],
            'expected_stage': case.get('expected_stage'), 'expected_code': case.get('reject_code'),
            'actual': actual, 'substitute_calls': model.calls if model else 0}


def evaluate(suite):
    results = [run_case(case) for case in suite['cases']]
    return {'status': 'passed' if results and all(r['matched'] for r in results) else 'failed',
            'total': len(results), 'matched': sum(r['matched'] for r in results), 'skipped': 0,
            'external_model_requests': 0, 'claim': 'proposal-validation fixtures, not live model or business accuracy',
            'results': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', nargs='?', type=Path, default=ROOT/'eval/understanding_v2/counterexamples_gate_v2.json')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    suite = json.loads(args.suite.read_text())
    report = evaluate(suite)
    from features import env_bool
    report.update(suite_version=suite['version'], suite_sha256=hashlib.sha256(args.suite.read_bytes()).hexdigest(),
                  semantic_regex_guards=env_bool('SEMANTIC_REGEX_GUARDS', True),
                  files_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [Path(__file__).resolve(), ROOT/'tcm/dataops/research_question.py',
                     ROOT/'tcm/dataops/query_coverage.py', ROOT/'tcm/intent_planner.py']})
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    for row in report['results']:
        print(row['id'], 'passed' if row['matched'] else 'FAILED', json.dumps(row['actual'], ensure_ascii=False))
    print(f"{report['matched']}/{report['total']} matched; status={report['status']}; skipped=0")
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
