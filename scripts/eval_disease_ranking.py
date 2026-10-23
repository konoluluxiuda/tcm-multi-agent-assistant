"""Freeze a local cohort and diagnose ranking bias without clinical labels or APIs."""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from time import monotonic

from tcm.disease_catalog import connect as disease_connect, dumps, identifier
from tcm.etcm_enrichment import connect as herb_connect
from tcm.herbs import SCHEMA_VERSION
from tcm.ranking_diagnostics import distribution, ranked_ids, summarize
from tcm.target_similarity import file_token, rank_sets, SnapshotChanged


def digest(targets):
    return hashlib.sha256(dumps(sorted(targets)).encode()).hexdigest()


def select_cohort(groups, sample_size):
    # Hash order is fixed before any candidate scores are inspected. One vote per
    # distinct nonempty disease target set, not per duplicated disease source.
    return [{'target_set_sha256': sha, 'record_id': min(ids), 'source_count': len(ids)}
            for sha, ids in sorted(groups.items())[:sample_size]]


def read_inputs(base_path, herb_path, disease_path, sample_size):
    with closing(herb_connect(herb_path)) as herb, closing(sqlite3.connect(
            Path(base_path).resolve().as_uri() + '?mode=ro', uri=True)) as base:
        disease, dm = disease_connect(disease_path)
        with closing(disease):
            for con in (herb, base, disease):
                con.execute('PRAGMA query_only=ON')
                con.execute('BEGIN')
            if base.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
                raise ValueError('Unknown base schema')
            hm = {r['key']: json.loads(r['value']) for r in herb.execute('SELECT key,value FROM metadata')}
            original = dict(base.execute("SELECT source_record_id,sha256 FROM records WHERE dataset_version=? AND state='valid'", (hm['dataset_version'],)))
            indexed = dict(herb.execute('SELECT source_record_id,file_sha256 FROM herbs WHERE dataset_version=?', (hm['dataset_version'],)))
            if not original or original != indexed:
                raise SnapshotChanged('Base and enrichment sources differ')
            herbs = {sid: set() for sid in indexed}
            for sid, uid in herb.execute("SELECT e.herb_id,n.identifier FROM edges e JOIN nodes n ON n.node_id=e.node_id WHERE e.kind='target' AND n.kind='target' AND n.identity_status LIKE 'source_declared_uniprot_not_externally_verified%' GROUP BY e.herb_id,n.identifier"):
                if sid not in herbs:
                    raise SnapshotChanged('Unknown herb source')
                if isinstance(uid, str) and identifier(uid) == uid:
                    herbs[sid].add(uid)
            universe = set().union(*herbs.values())
            binding = dm['herb_background']
            if any(binding[key] != hm[key] for key in ('dataset_version', 'source_fingerprint')) or binding['target_set_sha256'] != digest(universe):
                raise SnapshotChanged('Disease background differs')
            groups = defaultdict(list)
            empty = 0
            for sid, sha, count in disease.execute("SELECT record_id,json_extract(document,'$.target_set_sha256'),json_extract(document,'$.target_count') FROM diseases"):
                if count:
                    groups[sha].append(sid)
                else:
                    empty += 1
            cohort = select_cohort(groups, sample_size)
            queries = {}
            for item in cohort:
                sid = item['record_id']
                targets = {r[0] for r in disease.execute('SELECT DISTINCT uniprot FROM target_rows WHERE record_id=? AND uniprot IS NOT NULL', (sid,))}
                if digest(targets) != item['target_set_sha256']:
                    raise SnapshotChanged('Disease target set differs from profile')
                queries[sid] = targets
                item.update(target_count=len(targets), outside_background_count=len(targets - universe))
            names = dict(herb.execute("SELECT source_record_id,coalesce(json_extract(document,'$.fields.name_pinyin.value'),source_record_id) FROM herbs"))
            provenance = {
                'herb_dataset_version': hm['dataset_version'], 'herb_source_fingerprint': hm['source_fingerprint'],
                'disease_dataset_version': dm['dataset_version'], 'disease_source_fingerprint': dm['source_fingerprint'],
                'herb_target_set_sha256': digest(universe),
                'herb_sources': len(herbs), 'herb_sources_with_targets': sum(bool(s) for s in herbs.values()),
                'herb_distinct_targets': len(universe), 'disease_sources_without_targets': empty,
                'disease_sources_with_targets': sum(map(len, groups.values())),
                'unique_nonempty_disease_sets': len(groups),
                'identical_set_groups': sum(len(ids) > 1 for ids in groups.values()),
                'sources_in_identical_set_groups': sum(len(ids) for ids in groups.values() if len(ids) > 1),
            }
    return herbs, queries, cohort, provenance, names


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def evaluate(base, herbs_path, diseases, out, sample_size=128, top_k=10):
    if type(sample_size) is not int or not 2 <= sample_size <= 512:
        raise ValueError('sample_size must be between 2 and 512')
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError('top_k must be between 1 and 20')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    paths = (base, herbs_path, diseases)
    before = [file_token(p) for p in paths]
    herbs, queries, cohort, provenance, names = read_inputs(*paths, sample_size)
    protocol = {
        'version': 'disease_ranking_diagnostics_v1', 'top_k': top_k, 'requested_sample_size': sample_size,
        'selection': 'first_sha256_sorted_unique_nonempty_disease_sets_min_source_id',
        'weighting': 'one_vote_per_selected_distinct_set',
        'baseline': 'global_covered_herbs_sorted_by_descending_target_count_then_source_id_query_independent',
        'overlap': 'intersection_size_divided_by_fixed_k_including_short_or_empty_lists',
        'jaccard_denominator': 'full_disease_union_herb_including_outside_background_ids',
        'ties': 'production_fraction_order_then_secondary_score_then_source_id',
        'production_crosschecks': min(8, len(cohort)),
        'predictive_validity': 'not_evaluated_no_independent_labels',
        'cohort': cohort, 'sources': provenance,
    }
    # Persist the protocol and selected cohort before computing any rankings.
    write_json(out / 'protocol.json', protocol)
    rows = []
    for i, item in enumerate(cohort):
        sid = item['record_id']; targets = queries[sid]
        row = dict(item)
        for metric in ('jaccard', 'common_count'):
            row[metric] = ranked_ids(targets, herbs, metric, top_k)
            if i < protocol['production_crosschecks']:
                expected = [r['source_record_id'] for r in rank_sets(sid, {**herbs, sid: targets}, metric=metric, top_k=top_k)]
                if row[metric] != expected:
                    raise AssertionError('Production ranking differs')
        row['positive_overlap_candidates'] = sum(bool(targets & s) for s in herbs.values())
        rows.append(row)
    if before != [file_token(p) for p in paths]:
        raise SnapshotChanged('Input changed during evaluation')
    report = {
        'status': 'completed', 'scope': 'label_free_development_diagnostics',
        'protocol_sha256': hashlib.sha256((out / 'protocol.json').read_bytes()).hexdigest(),
        'sources': provenance, 'sample_size': len(rows), 'top_k': top_k,
        'sample_target_counts': distribution(r['target_count'] for r in rows),
        'sample_outside_background_counts': distribution(r['outside_background_count'] for r in rows),
        'positive_overlap_candidates': distribution(r['positive_overlap_candidates'] for r in rows),
        'production_rankings_checked': protocol['production_crosschecks'] * 2,
        'diagnostics': summarize(rows, herbs, top_k),
        'predictive_validity': 'not_evaluated_no_independent_labels',
        'input_stat_tokens_unchanged': True, 'duration_seconds': round(monotonic() - started, 3),
    }
    for method in report['diagnostics']['methods'].values():
        for field in ('most_frequent_top1', 'most_frequent_topk'):
            method[field] = [{'source_record_id': sid, 'name_pinyin': names[sid], 'queries': count} for sid, count in method[field]]
    write_json(out / 'rankings.json', rows)
    write_json(out / 'summary.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--enrichment', type=Path, required=True)
    parser.add_argument('--diseases', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='New output directory; never overwrites an existing run')
    parser.add_argument('--sample-size', type=int, default=128)
    parser.add_argument('--top-k', type=int, default=10)
    args = parser.parse_args()
    result = evaluate(args.database, args.enrichment, args.diseases, args.out, args.sample_size, args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
