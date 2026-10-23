"""Ranking denominators, selection, read-only snapshots and honest metric scope."""
import json
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.eval_disease_ranking import digest, evaluate, select_cohort
from tcm.herbs import SCHEMA_VERSION
from tcm.ranking_diagnostics import ranked_ids, overlap_at_k, summarize
from tcm.target_similarity import rank_sets, SnapshotChanged


class RankingDiagnosticsTests(unittest.TestCase):
    def test_scores_match_production_with_ties_and_outside_targets(self):
        rng = random.Random(73)
        herbs = {str(i): set(rng.sample(range(30), rng.randrange(20))) for i in range(50)}
        herbs['duplicate'] = herbs['1'].copy()
        for size in (0, 1, 10, 30):
            query = set(rng.sample(range(40), size))
            for metric in ('jaccard', 'common_count'):
                expected = rank_sets('query', {**herbs, 'query': query}, metric=metric)
                self.assertEqual(ranked_ids(query, herbs, metric), [r['source_record_id'] for r in expected])

    def test_baseline_is_query_independent_and_excludes_empty(self):
        herbs = {'a': {'A'}, 'b': {'B', 'C'}, 'c': set(), 'd': {'D'}}
        self.assertEqual(ranked_ids({'A'}, herbs, 'size_only'), ['b', 'a', 'd'])
        self.assertEqual(ranked_ids(set(), herbs, 'size_only'), ['b', 'a', 'd'])
        self.assertEqual(ranked_ids(set(), herbs, 'jaccard'), [])

    def test_outside_background_affects_union_and_order(self):
        herbs = {'small': {'A'}, 'large': {'A', 'B', 'C'}}
        self.assertEqual(ranked_ids({'A', 'B'}, herbs, 'jaccard'), ['large', 'small'])
        self.assertEqual(overlap_at_k(['a'], ['a'], 10), .1)
        self.assertEqual(overlap_at_k([], [], 10), 0)

    def test_summary_denominators_include_empty_queries(self):
        rows = [{'jaccard': ['a'], 'common_count': ['b']}, {'jaccard': [], 'common_count': ['b']}]
        result = summarize(rows, {'a': {1}, 'b': {1, 2}}, 2)
        j = result['methods']['jaccard']
        self.assertEqual(j['overlap_with_size_only_at_k']['mean'], .25)
        self.assertEqual(j['pairwise_overlap_at_k']['mean'], 0)
        self.assertEqual(j['empty_results'], 1)
        self.assertIsNone(summarize([], {}, 2)['between_methods_overlap_at_k']['mean'])

    def test_cohort_selection_deduplicates_and_is_order_independent(self):
        groups = {'b': ['z'], 'a': ['y', 'x']}
        selected = select_cohort(groups, 1)
        self.assertEqual(selected, [{'target_set_sha256': 'a', 'record_id': 'x', 'source_count': 2}])
        self.assertEqual(selected, select_cohort(dict(reversed(list(groups.items()))), 1))

    def fixture(self, root):
        paths = [root / name for name in ('base.db', 'herb.db', 'disease.db')]
        with sqlite3.connect(paths[0]) as db:
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            db.execute('CREATE TABLE records(source_record_id,sha256,dataset_version,state)')
            db.execute("INSERT INTO records VALUES ('h','hash','v','valid')")
        hm = {'kind': 'etcm_enrichment_v1', 'build_complete': True, 'dataset_version': 'v', 'source_fingerprint': 'fingerprint'}
        with sqlite3.connect(paths[1]) as db:
            db.executescript('CREATE TABLE metadata(key,value); CREATE TABLE herbs(source_record_id,file_sha256,dataset_version,document); CREATE TABLE nodes(node_id,identifier,kind,identity_status); CREATE TABLE edges(herb_id,node_id,kind);')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [(k, json.dumps(v)) for k, v in hm.items()])
            db.execute("INSERT INTO herbs VALUES ('h','hash','v','{}')")
            db.execute("INSERT INTO nodes VALUES ('n','P12345','target','source_declared_uniprot_not_externally_verified')")
            db.execute("INSERT INTO edges VALUES ('h','n','target')")
        dm = {'kind': 'etcm_disease_genes_v1', 'build_complete': True, 'dataset_version': 'dv', 'source_fingerprint': 'df',
              'herb_background': {**hm, 'target_set_sha256': digest({'P12345'})}}
        with sqlite3.connect(paths[2]) as db:
            db.executescript('PRAGMA user_version=1; CREATE TABLE metadata(key,value); CREATE TABLE diseases(record_id,document); CREATE TABLE target_rows(record_id,uniprot);')
            db.executemany('INSERT INTO metadata VALUES (?,?)', [(k, json.dumps(v)) for k, v in dm.items()])
            for sid, targets in [('d1', {'P12345'}), ('d2', {'P12345', 'Q12345'}), ('d3', set()), ('d4', {'P12345'})]:
                db.execute('INSERT INTO diseases VALUES (?,?)', (sid, json.dumps({'target_count': len(targets), 'target_set_sha256': digest(targets)})))
                db.executemany('INSERT INTO target_rows VALUES (?,?)', [(sid, uid) for uid in targets])
        return paths

    def test_evaluation_is_readonly_reproducible_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); paths = self.fixture(root)
            before = [p.read_bytes() for p in paths]
            a = evaluate(*paths, root / 'a', sample_size=2)
            b = evaluate(*paths, root / 'b', sample_size=2)
            self.assertEqual(a['diagnostics'], b['diagnostics'])
            self.assertEqual(a['protocol_sha256'], b['protocol_sha256'])
            self.assertEqual(a['sources']['identical_set_groups'], 1)
            self.assertEqual(a['sample_size'], 2)
            self.assertEqual(a['production_rankings_checked'], 4)
            self.assertEqual(before, [p.read_bytes() for p in paths])
            self.assertEqual(a['predictive_validity'], 'not_evaluated_no_independent_labels')
            with self.assertRaises(FileExistsError):
                evaluate(*paths, root / 'a')

    def test_wrong_background_and_changed_snapshot_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); paths = self.fixture(root)
            with patch('scripts.eval_disease_ranking.file_token', side_effect=[1, 1, 1, 1, 1, 2]):
                with self.assertRaises(SnapshotChanged):
                    evaluate(*paths, root / 'changed')
            self.assertFalse((root / 'changed/summary.json').exists())
            with sqlite3.connect(paths[1]) as db:
                db.execute("UPDATE nodes SET identifier='Q12345'")
            with self.assertRaises(SnapshotChanged):
                evaluate(*paths, root / 'bad_background')

    def test_invalid_arguments_and_corrupt_selected_set_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); paths = self.fixture(root)
            with self.assertRaises(ValueError):
                evaluate(*paths, root / 'bad', top_k=True)
            self.assertFalse((root / 'bad').exists())
            with sqlite3.connect(paths[2]) as db:
                db.execute("UPDATE target_rows SET uniprot='O12345'")
            with self.assertRaises(SnapshotChanged):
                evaluate(*paths, root / 'corrupt')


if __name__ == '__main__':
    unittest.main()
