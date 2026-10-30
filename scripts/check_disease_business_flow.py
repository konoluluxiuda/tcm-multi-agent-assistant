"""Exercise real local tools with synthetic catalogs; no model planning claim."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

from scripts.build_etcm_diseases import build
from tests.test_relation_queries import RelationQueryTests
from tests.test_disease_catalog import document
from tcm.analysis_tools import invoke
from tcm.disease_catalog import verify_witness


def check():
    fixture = RelationQueryTests()
    transcript, checks = [], {}

    def require(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def call(tool, **arguments):
        result = invoke({'tool': tool, 'arguments': arguments}).model_dump(mode='json')
        transcript.append({'tool': tool, 'arguments': arguments, 'result': result})
        require('no_tool_error', result['status'] != 'error')
        return result['data']

    def discover(name):
        data = call('search_disease_sources', name=name)
        exact = [item for item in data['items'] if item['match_kind'] == 'exact']
        # Never silently choose a partial page or a fuzzy/ambiguous match.
        return exact[0]['record_id'] if len(exact) == 1 and not data['has_next'] else None

    try:
        fixture.setUp()
        source = fixture.root / 'disease-source'
        directory = source / 'etcm_diseases'
        directory.mkdir(parents=True)
        for name, ids in [('Synthetic disease', ['P00000', 'Q00000', 'R99999']),
                          ('Same set', ['P00000', 'Q00000', 'R99999']),
                          ('Empty', []), ('Disjoint', ['R88888'])]:
            raw = document(name)
            rows = [{'UniProt Accession Number': [value], 'Gene Symbol': ['X'+str(i)]}
                    for i, value in enumerate(ids)]
            if name == 'Synthetic disease':
                rows *= 8
            raw['data'][1]['value'][0]['value'] = rows
            raw['complete_fetch_summary'][0].update(actual=len(rows), expected=len(rows))
            (directory / (name.replace(' ', '_')+'.json')).write_text(json.dumps(raw))
        with redirect_stdout(io.StringIO()):
            build(source, fixture.database, fixture.root / 'diseases', 'disease-v1')
        database = fixture.root / 'diseases/diseases.sqlite3'
        with patch.dict(os.environ, {'ETCM_DISEASE_DB': str(database)}):
            selected = discover('Synthetic disease')
            require('source_discovered', bool(selected))
            pages = []
            for page in range(1, 11):
                data = call('disease_gene_profile', name=selected, page=page)
                pages.append(data)
                if not data['has_next']:
                    break
            require('all_pages_read', not pages[-1]['has_next'] and
                    [len(p['rows']) for p in pages] == [20, 4])
            require('unique_target_count', len(pages[0]['target_ids']) == 3)
            require('page_identity_stable', all(p['record_id'] == selected and
                    p['catalog_revision'] == pages[0]['catalog_revision'] for p in pages))
            for p in pages:
                for row in p['rows']:
                    verify_witness(source, p['profile'], row)
            rank = call('rank_herbs_for_gene_set', name=selected, top_k=5, metric='jaccard')
            require('ranking_bound_to_source', rank['record_id'] == selected)
            require('only_available_candidates_returned', len(rank['items']) == 2)
            require('ranking_math', [r['shared_count'] for r in rank['items']] == [2, 1]
                    and [r['jaccard'] for r in rank['items']] == [2/25, 1/26])
            require('outside_background_retained', rank['outside_background_ids'] == ['R99999'])
            witness_count = 0
            for item in rank['items']:
                require('evidence_covers_shared_ids_' + item['source']['name'],
                        len(item['evidence']) == item['shared_count'])
                herb_path = fixture.root / 'source' / item['source']['source']['relative_file_path']
                herb = json.loads(herb_path.read_text())
                for pair in item['evidence']:
                    require('nonempty_witness_pair', bool(pair['disease_rows'] and pair['herb_rows']))
                    for row in pair['disease_rows']:
                        verify_witness(source, rank['profile'], row)
                        witness_count += 1
                    for row in pair['herb_rows']:
                        raw = herb
                        for token in row['json_pointer'].split('/')[1:]:
                            raw = raw[int(token)] if isinstance(raw, list) else raw[token]
                        require('herb_original_row_matches', raw == row['raw_row'])
                        witness_count += 1
            for name in ('Empty', 'Disjoint'):
                sid = discover(name)
                data = call('rank_herbs_for_gene_set', name=sid, top_k=5)
                require(name + '_no_fabricated_candidates', not data['items'])
            require('unknown_stops_at_search', discover('Missing disease') is None)
            require('prefix_requires_selection', discover('Synthetic') is None)
            with sqlite3.connect(database) as con:
                con.execute("UPDATE diseases SET name_key='synthetic disease' WHERE name='Same set'")
            require('ambiguous_stops_at_search', discover('Synthetic disease') is None)
        return {'status': 'passed', 'mode': 'deterministic_business_harness',
                'live_model_calls': 0, 'data': 'synthetic_only', 'checks': checks,
                'verified_ranking_witness_rows': witness_count, 'transcript': transcript,
                'limitations': ['Not a model planning or chat end-to-end evaluation',
                                'Not clinical prediction or independent holdout evaluation',
                                'No integration with the experimental offload loop']}
    finally:
        fixture.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = check()
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__), *(root / 'tcm').glob('*.py'),
             root / 'tests/test_relation_queries.py', root / 'tests/test_disease_catalog.py',
             root / 'scripts/build_etcm_diseases.py', root / 'scripts/build_etcm_enrichment.py']
    report['source_sha256'] = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in paths}
    (args.out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'checks': len(report['checks']),
                      'tool_calls': len(report['transcript']), 'live_model_calls': 0}))


if __name__ == '__main__':
    main()
