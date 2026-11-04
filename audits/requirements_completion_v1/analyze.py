"""Export aggregate receipts only; keep corpus and provider payloads in var/."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3


def analyze(run):
    report_path = sorted((run / 'reports').glob('*.json'))[-1]
    report = json.loads(report_path.read_text())
    groups = {}
    for label, seen in [('seen_regression', True), ('previously_unrun', False)]:
        group = groups[label] = {}
        for policy in ('R', 'G', 'V'):
            rows = [r for r in report['scores'] if r['policy'] == policy
                    and (int(r['scenario_id'].split('-')[1]) <= 4) == seen]
            group[policy] = {
                kind: {'passed': sum(r['completed'] for r in rows if r['task_class'] == kind),
                       'total': sum(r['task_class'] == kind for r in rows)}
                for kind in ('supported', 'boundary')}
            group[policy].update(passed=sum(r['completed'] for r in rows), total=len(rows),
                                 run_status=dict(Counter(r['run_status'] for r in rows)),
                                 terminal_states=dict(Counter(r['terminal_state'] for r in rows)))
    with sqlite3.connect(f'file:{run / "ledger.sqlite3"}?mode=ro', uri=True) as con:
        states = con.execute('SELECT id,policy,status FROM cases').fetchall()
        requests = con.execute('SELECT policy,status,http FROM dispatches').fetchall()
    failures = []
    for row in report['scores']:
        if row['completed']:
            continue
        folder = run / 'cases' / f'{row["scenario_id"]}_{row["policy"]}'
        codes = set()
        stages = set()
        for receipt in folder.glob('*-response.private.json'):
            raw = json.loads(receipt.read_text())
            for container in (raw, raw.get('assistant', {}), raw.get('proposal', {})):
                failure = container.get('failure') or {}
                if failure.get('code'):
                    codes.add(failure['code'])
                if failure.get('stage'):
                    stages.add(failure['stage'])
        failures.append({**row, 'public_failure_codes': sorted(codes),
                         'public_failure_stages': sorted(stages)})
    return {
        'date': '2026-11-04', 'run': str(run),
        'manifest_sha256': hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest(),
        'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
        'groups': groups, 'by_policy': report['by_policy'],
        'efficiency': report['efficiency'], 'dispatched': len(requests),
        'received_http_200': sum(status == 'received' and http == 200 for _, status, http in requests),
        'unknown_outcomes': report['unknown_outcomes'], 'provider_stopped': report['provider_stopped'],
        'known_total_tokens': report['known_total_tokens'], 'usage_unknown': report['usage_unknown'],
        'started_combinations': len(states), 'planned_combinations': 120,
        'interrupted': [{'scenario_id': s, 'policy': p} for s, p, state in states if state == 'started'],
        'not_started_combinations': 120 - len(states),
        'failures': failures, 'automatic_retries': 0, 'default_policy_changed': False,
        'independent_generalization_proven': False,
        'scope': 'Independent AI wording; source consistency only; not a human user or clinical study',
        'raw_provider_payloads_or_source_values_exported': False,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.run)
    with args.out.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ('groups', 'dispatched', 'unknown_outcomes')}, ensure_ascii=False))
