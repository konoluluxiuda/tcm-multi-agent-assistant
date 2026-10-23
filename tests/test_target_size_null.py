"""Size-null math, all-candidate winner selection and snapshot-bound execution."""
from itertools import combinations
import json
from pathlib import Path
import random
from statistics import mean, pvariance
import tempfile
import unittest
from unittest.mock import patch

from tests import test_ranking_diagnostics as fixtures
from scripts.eval_disease_ranking import evaluate as baseline_evaluate
from scripts.eval_disease_size_null import evaluate, load_baseline
from tcm.target_similarity import SnapshotChanged
from tcm.target_size_null import diagnose, overlap_moments, nearest_rank_95


class SizeNullTests(unittest.TestCase):
    def test_exact_moments_match_exhaustive_small_populations(self):
        self.assertEqual(overlap_moments(0, 0, 0), (0, 0))
        for n in range(1, 7):
            for disease_size in range(n + 1):
                for herb_size in range(n + 1):
                    herb = set(range(herb_size))
                    overlaps = [len(herb & set(draw)) for draw in combinations(range(n), disease_size)]
                    expected, variance = overlap_moments(n, disease_size, herb_size)
                    self.assertAlmostEqual(expected, mean(overlaps))
                    self.assertAlmostEqual(variance, pvariance(overlaps))

    def test_seeded_simulation_matches_plain_sets_including_unselected_winners(self):
        herbs = {'observed_winner': {'A', 'B'}, 'other': {'C', 'D', 'E', 'F'}, 'empty': set()}
        result = diagnose({'A', 'B'}, herbs, seed=7, iterations=32, top_k=1)
        self.assertEqual(result['methods']['common_count'], ['observed_winner'])
        rng = random.Random(7); maxima = []; observed_candidate = []
        for _ in range(32):
            draw = set(rng.sample(list('ABCDEF'), 2))
            maxima.append(max(len(draw & targets) for targets in herbs.values()))
            observed_candidate.append(len(draw & herbs['observed_winner']))
        self.assertEqual(result['null_maxima'], maxima)
        candidate = result['selected_candidates']['observed_winner']
        self.assertEqual(candidate['simulated_shared_counts']['mean'], mean(observed_candidate))
        self.assertGreater(mean(maxima), mean(observed_candidate))
        self.assertEqual(result['null_max_ge_observed'], sum(x >= 2 for x in maxima))

    def test_outside_targets_do_not_enlarge_draw_but_remain_in_jaccard(self):
        herbs = {'small': {'A'}, 'large': set('ABCDE')}
        inside = diagnose({'A', 'B'}, herbs, iterations=16)
        outside = diagnose({'A', 'B', 'X', 'Y'}, herbs, iterations=16)
        self.assertEqual(inside['null_maxima'], outside['null_maxima'])
        self.assertEqual(outside['query_outside_background_count'], 2)
        self.assertEqual(inside['methods']['jaccard'][0], 'small')
        self.assertEqual(outside['methods']['jaccard'][0], 'large')
        self.assertEqual(inside['selected_candidates']['large']['expected_shared_count'],
                         outside['selected_candidates']['large']['expected_shared_count'])

    def test_order_independence_and_zero_variance(self):
        herbs = {'b': {'B'}, 'a': {'A'}}
        a = diagnose({'A', 'B'}, herbs, iterations=16)
        b = diagnose({'B', 'A'}, dict(reversed(list(herbs.items()))), iterations=16)
        self.assertEqual(a, b)
        self.assertEqual(a['selected_candidates']['a']['null_variance'], 0)
        self.assertIsNone(a['selected_candidates']['a']['standardized_excess'])
        self.assertEqual(a['null_max_ge_observed'], 16)
        self.assertEqual(diagnose({'X'}, herbs, iterations=16)['status'], 'no_background_overlap')
        self.assertEqual(diagnose(set(), {}, iterations=16)['null_maxima'], [0] * 16)
        self.assertEqual(nearest_rank_95(list(range(1, 21))), 19)

    def test_invalid_sizes_and_budgets_are_rejected(self):
        for args in ((1, 2, 1), (0, 1, 0), (-1, 0, 0), (True, 1, 1)):
            with self.assertRaises(ValueError): overlap_moments(*args)
        for options in ({'iterations': 1}, {'iterations': 1025}, {'seed': -1}, {'top_k': True}):
            with self.assertRaises(ValueError): diagnose(set(), {}, **options)

    def setup_inputs(self, root):
        paths = fixtures.RankingDiagnosticsTests().fixture(root)
        baseline = root / 'baseline'
        baseline_evaluate(*paths, baseline, sample_size=2)
        return paths, baseline

    def test_end_to_end_reproducible_readonly_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); paths, baseline = self.setup_inputs(root)
            before = [p.read_bytes() for p in paths]
            a = evaluate(*paths, baseline, root / 'a', iterations=16)
            b = evaluate(*paths, baseline, root / 'b', iterations=16)
            self.assertEqual(a['protocol_sha256'], b['protocol_sha256'])
            self.assertEqual((root / 'a/queries.json').read_bytes(), (root / 'b/queries.json').read_bytes())
            self.assertEqual(before, [p.read_bytes() for p in paths])
            self.assertEqual(a['candidate_comparisons'], 32)
            self.assertEqual(a['diagnostics']['queries_above_all_simulated_maxima'], 0)
            self.assertEqual(a['predictive_validity'], 'not_evaluated_no_independent_labels')
            with self.assertRaises(FileExistsError): evaluate(*paths, baseline, root / 'a', iterations=16)

    def test_baseline_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _, baseline = self.setup_inputs(root)
            path = baseline / 'protocol.json'; data = json.loads(path.read_text())
            data['cohort'][0]['target_count'] += 1
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): load_baseline(baseline)

    def test_snapshot_change_and_work_limit_never_publish_completion(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); paths, baseline = self.setup_inputs(root)
            with patch('scripts.eval_disease_size_null.file_token', side_effect=[1, 1, 1, 1, 1, 2]):
                with self.assertRaises(SnapshotChanged): evaluate(*paths, baseline, root / 'changed', iterations=16)
            self.assertFalse((root / 'changed/summary.json').exists())
            with patch('scripts.eval_disease_size_null.MAX_COMPARISONS', 1):
                with self.assertRaises(ValueError): evaluate(*paths, baseline, root / 'budget', iterations=16)
            self.assertFalse((root / 'budget/protocol.json').exists())

    def test_baseline_source_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); paths, baseline = self.setup_inputs(root)
            from scripts.eval_disease_ranking import read_inputs
            herbs, queries, cohort, sources, names = read_inputs(*paths, 2)
            sources['disease_source_fingerprint'] = 'changed'
            with patch('scripts.eval_disease_size_null.read_inputs', return_value=(herbs, queries, cohort, sources, names)):
                with self.assertRaises(SnapshotChanged): evaluate(*paths, baseline, root / 'mismatch', iterations=16)


if __name__ == '__main__':
    unittest.main()
