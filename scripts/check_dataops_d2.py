"""Local-only D2 development acceptance; synthetic driver, immutable output directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from tcm.dataops.contracts import Goal, LABELS
from tcm.dataops.data_access import Sources, fingerprint
from tcm.dataops.graph import Engine
from tcm.dataops.model import SyntheticDriver
from tcm.dataops.workspace import Workspaces

ROOT = Path(__file__).resolve().parents[1]


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def phase(out, name):
    """Two separate Python processes, not just two Engine instances."""
    out.mkdir(parents=True, exist_ok=True)
    from tests.dataops_fixture import make_sources
    store = Workspaces(out / 'work')
    if name == 'wait':
        _, factory = make_sources(out / 'fixture')
        goal = Goal(query={'names': ['Gamma'], 'limit': 50})
        with factory() as src:
            doc, _ = store.reserve_agent('fixture-owner', 'cross-process-test', goal.model_dump(), src.revision,
                                          question='核对丙草的功能与主治')
        waiting = Engine(store, 'fixture-owner', doc['id'], factory, SyntheticDriver()).run()
        assert waiting['status'] == 'waiting_input'
        write(out / 'waiting.json', {'pid': os.getpid(), 'workspace': waiting})
    else:
        previous = json.loads((out / 'waiting.json').read_text())
        doc = previous['workspace']; pending = doc['pending']
        fixture = out / 'fixture'
        factory = lambda: Sources(fixture / 'herbs.sqlite3', fixture / 'enrichment.sqlite3', fixture / 'index', 'fixture-v1')
        answer = {'question_id': pending['question_id'], 'revision': 1, 'operation': 'choose',
                  'candidate_id': pending['candidates'][0]['entry_id']}
        result = Engine(store, 'fixture-owner', doc['id'], factory, SyntheticDriver()).run(answer)
        assert result['status'] == 'complete' and result['progress']['model_calls'] == 7
        assert result['result']['rows'][0]['cells']['功能与主治']['value'] == '测试原文2'
        assert previous['pid'] != os.getpid()
        write(out / 'summary.json', {'status': 'passed', 'different_processes': True, 'wait_pid': previous['pid'],
                                    'resume_pid': os.getpid(), 'model_decisions_total': 7,
                                    'external_model_calls': 0, 'selected_candidate': 'entry-2'})


def check(out, reference_dir):
    if out.exists():
        raise FileExistsError('Use a fresh directory; earlier results are immutable')
    out.mkdir(parents=True)
    protocol = json.loads((reference_dir / 'protocol.json').read_text())
    reference = json.loads((reference_dir / 'reference.json').read_text())
    goal = Goal.model_validate(protocol['goal'])
    index = ROOT / 'var/mineru_retrieval_v2'
    factory = lambda: Sources(ROOT / 'var/tcm/herbs.sqlite3',
        ROOT / 'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3', index)
    view_path = ROOT / 'var/mineru_revisions_v2/research_view.json'
    receipt = json.loads((index / 'manifest.json').read_text())
    assert fingerprint(view_path) == receipt['view_sha256']
    entries = {e['id']: e for e in json.loads(view_path.read_text())['entries']}
    expanded = json.loads(json.dumps(reference))
    for sid, expected in expanded.items():
        ids = expected['candidate_ids']
        parts = [f['raw_text'] for f in entries[ids[0]]['fields'] if f['scope'] in ('monograph', 'prepared_slices')
                 and f['label'] == LABELS['贮藏'] and f['raw_text'].strip()] if len(ids) == 1 else []
        expected['fields']['贮藏'] = parts[0] if len(parts) == 1 else None
    # Freeze independent pre-index values BEFORE running v1 or v2.
    with factory() as src:
        assert src.revision == protocol['source_revision']
        version = src.revision
    write(out / 'protocol.json', {'kind': 'dataops-d2-development-local-v1', 'goal': goal.model_dump(),
        'source_revision': version, 'prior_reference_sha256': fingerprint(reference_dir / 'reference.json'),
        'source_code': {str(p.relative_to(ROOT)): fingerprint(p) for p in (ROOT / 'tcm/dataops').glob('*.py')},
        'driver': SyntheticDriver.label, 'external_model_calls': 0,
        'reference_frozen_before_execution': True, 'review_status': 'source_reconciled_pending_human_signoff'})
    write(out / 'reference.json', expanded)
    store = Workspaces(out / 'work')
    doc, _ = store.reserve_agent('local-owner', 'd2-real-v1', goal.model_dump(), version, question='核对指定20条资料')
    first = Engine(store, 'local-owner', doc['id'], factory, SyntheticDriver()).run()
    assert first['status'] == 'complete'
    original, _ = store.artifact('local-owner', first['id']); original_hash = fingerprint(original)
    v2_goal = goal.model_copy(update={'document_fields': goal.document_fields + ['贮藏']})
    child, _ = store.reserve_agent('local-owner', 'd2-real-v2', v2_goal.model_dump(), version,
        question='增加贮藏字段，保留其余结果，生成第二版', parent_id=first['id'], expected_revision=1)
    second = Engine(store, 'local-owner', child['id'], factory, SyntheticDriver()).run()
    assert second['status'] == 'complete' and second['revision'] == 2
    for result, ref in ((first['result'], reference), (second['result'], expanded)):
        assert {r['source_id'] for r in result['rows']} == set(ref)
        for row in result['rows']:
            for field, cell in row['cells'].items():
                assert cell['value'] == ref[row['source_id']]['fields'][field], (row['source_id'], field)
    reads = [e for e in second['progress']['events'] if e['tool'] == 'lookup_document_fields']
    assert all(item['fields'] == ['贮藏'] for event in reads for item in event['reads'])
    assert not any(e['tool'] == 'inspect_sources' for e in second['progress']['events'])
    assert fingerprint(original) == original_hash
    with factory() as src:
        assert src.revision == version
    for name in ('wait', 'resume'):
        subprocess.run([sys.executable, '-m', 'scripts.check_dataops_d2', '--phase', name,
                        '--out', str(out / 'restart')], check=True, cwd=ROOT)
    report = {'status': 'passed', 'driver': SyntheticDriver.label, 'records': 20,
        'v1_source_field_checks': 80, 'v2_source_field_checks': 100,
        'v1_workspace': first['id'], 'v2_workspace': second['id'], 'v1_preserved': True,
        'v2_reused_cells': second['progress']['reused_cells'], 'v2_leaf_reads': second['progress']['leaf_reads'],
        'v2_diff_count': len(second['result']['diff']), 'source_catalogs_unchanged': True,
        'external_model_calls': 0, 'true_model_effectiveness': 'not_run',
        'cross_process_hitl': json.loads((out / 'restart/summary.json').read_text()),
        'review_status': 'source_reconciled_pending_human_signoff'}
    write(out / 'summary.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--reference', type=Path, default=ROOT / 'var/dataops_d1_scope_final')
    parser.add_argument('--phase', choices=['wait', 'resume'])
    args = parser.parse_args()
    phase(args.out.resolve(), args.phase) if args.phase else check(args.out.resolve(), args.reference)
