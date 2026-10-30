"""Freeze a 20-record source reference, then verify the local F baseline against it."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from tcm.dataops.data_access import Sources, canonical, fingerprint
from tcm.dataops.contracts import Goal, Query, LABELS
from tcm.dataops.workspace import Workspaces
from tcm.dataops.baseline import execute
from tcm.dataops.artifacts import verify
from tcm.herbs import parse_herb

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def check(args):
    if args.out.exists():
        raise FileExistsError('Use a fresh output directory; do not overwrite earlier results')
    args.out.mkdir(parents=True)
    with Sources(args.herbs, args.enrichment, args.index) as sources:
        view = json.loads(args.view.read_text())
        receipt = json.loads((args.index / 'manifest.json').read_text())
        if fingerprint(args.view) != receipt['view_sha256']:
            raise ValueError('Reference view does not correspond to index')
        entries = {e['id']: e for e in view['entries']}
        docs = {d['source_record_id']: d for (raw,) in sources.h.execute(
            'SELECT document FROM records WHERE state=\'valid\' AND dataset_version=? ORDER BY source_record_id', (sources.version,))
                for d in [json.loads(raw)]}
        categories = {'existing_identity_comparison': [], 'name_candidate_only': [], 'no_name_candidate': []}
        for sid in docs:
            candidate = sources.e.execute('SELECT 1 FROM name_candidates WHERE herb_id=? LIMIT 1', (sid,)).fetchone()
            comparison = sources.e.execute('SELECT 1 FROM identity_comparisons WHERE herb_id=? LIMIT 1', (sid,)).fetchone()
            categories['existing_identity_comparison' if comparison else 'name_candidate_only' if candidate else 'no_name_candidate'].append(sid)
        picked = (categories['existing_identity_comparison'][:3] + categories['name_candidate_only'][:8] + categories['no_name_candidate'][:9])
        if len(picked) != 20:
            raise ValueError('Expected 20 records with the defined coverage; revise protocol explicitly')
        goal = Goal(query=Query(source_ids=sorted(picked)), attributes=['medicinal_part', 'latin_name'],
                    document_fields=['功能与主治', '注意'])
        reference = {}
        for sid in sorted(picked):
            source = docs[sid]['source']
            path = (args.raw / source['relative_file_path']).resolve()
            if not path.is_relative_to(args.raw.resolve()):
                raise ValueError('Source path outside raw root')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != source['file_sha256']:
                raise ValueError('Raw herb and database differ')
            record = parse_herb(raw, source['relative_file_path'], sources.version)
            expected = {f: record['fields'][f]['value'] for f in goal.attributes}
            candidates = [r[0] for r in sources.e.execute('SELECT entry_id FROM name_candidates WHERE herb_id=? ORDER BY entry_id', (sid,))]
            for field in goal.document_fields:
                # Read the pre-index research view, not the tested lookup output.
                parts = [f['raw_text'] for f in entries[candidates[0]]['fields']
                         if f['scope'] in ('monograph', 'prepared_slices') and f['label'] == LABELS[field] and f['text'].strip()] if len(candidates) == 1 else []
                expected[field] = parts[0] if len(parts) == 1 else None
            reference[sid] = {'source_sha256': source['file_sha256'], 'candidate_ids': candidates, 'fields': expected}
        protocol = {'version': 'dataops-d1-source-reference-v1', 'source_revision': sources.revision,
                    'review_status': 'source_reconciled_pending_human_signoff',
                    'selection_counts': {k: len(set(v) & set(picked)) for k, v in categories.items()},
                    'goal': goal.model_dump(), 'view_sha256': fingerprint(args.view),
                    'source_code': {str(p.relative_to(ROOT)): fingerprint(p) for p in (ROOT / 'tcm/dataops').glob('*.py')},
                    'external_model_calls': 0, 'purpose': 'development_source_consistency_not_independent_business_accuracy'}
        # Freeze expected values before invoking the implementation.
        write(args.out / 'protocol.json', protocol); write(args.out / 'reference.json', reference)
        store = Workspaces(args.out / 'workspace')
        cases = []
        for number, ids in enumerate((sorted(picked), sorted(picked)[:5], sorted(picked)[5:10], sorted(picked)[10:15], sorted(picked)[15:])):
            case_goal = goal.model_copy(update={'query': Query(source_ids=ids)})
            doc, created = execute(store, 'local-d1-owner', 'd1-case-' + str(number), sources, case_goal)
            result = doc['result']
            assert {r['source_id'] for r in result['rows']} == set(ids)
            checked = 0
            for row in result['rows']:
                for field, expected in reference[row['source_id']]['fields'].items():
                    assert row['cells'][field]['value'] == expected, (row['source_id'], field)
                    checked += 1
            path, _ = store.artifact('local-d1-owner', doc['id'])
            verification = verify(path, result)
            replay, again = execute(store, 'local-d1-owner', 'd1-case-' + str(number), sources, case_goal)
            assert not again and replay['id'] == doc['id'] and created
            cases.append({'case': number, 'workspace_id': doc['id'], 'status': 'passed', 'records': len(ids),
                          'fields_compared_to_reference': checked, 'workbook': verification,
                          'mapping_states': result['coverage']['mapping_states'], 'missing_cells': result['coverage']['missing_cells']})
        assert [fingerprint(p) for p in sources.paths] == sources.revision['sources']
        summary = {'status': 'passed', 'source_records': 20, 'development_cases': cases,
                   'review_status': protocol['review_status'], 'raw_and_catalogs_unchanged': True,
                   'external_model_calls': 0, 'reference_frozen_before_execution': True,
                   'unique_source_field_checks': 80, 'independent_model_accuracy': 'not_evaluated'}
        write(args.out / 'summary.json', summary)
        print(json.dumps({'status': summary['status'], 'source_records': 20, 'cases': len(cases),
                          'out': str(args.out), 'model_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--herbs', type=Path, default=ROOT / 'var/tcm/herbs.sqlite3')
    parser.add_argument('--enrichment', type=Path, default=ROOT / 'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3')
    parser.add_argument('--index', type=Path, default=ROOT / 'var/mineru_retrieval_v2')
    parser.add_argument('--view', type=Path, default=ROOT / 'var/mineru_revisions_v2/research_view.json')
    parser.add_argument('--raw', type=Path, default=ROOT.parent / 'HDCTI/dataset/ETCM2.0')
    check(parser.parse_args())
