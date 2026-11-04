"""Feedback evaluation is explicit, independently budgeted and cannot reuse old caps."""
import json
from pathlib import Path
import tempfile
import unittest
from scripts import eval_requirements_generalization as runner
from tcm.evaluation.ledger import Ledger
from tests.test_requirements_benchmark import scenario, result


class FeedbackProtocolTests(unittest.TestCase):
    def test_legacy_policy_budget_unchanged(self):
        self.assertEqual(runner._budget([scenario()]),
                         {'R': 0, 'G': 1, 'V': 2, 'total': 3, 'turns': 1, 'scenarios': 1})

    def test_selected_comparison_uses_four_calls_per_turn_not_old_budget(self):
        self.assertEqual(runner._budget([scenario()], ('G', 'W')),
                         {'G': 1, 'W': 3, 'total': 4, 'turns': 1, 'scenarios': 1})
        for policies in ((), ('W', 'W'), ('X',)):
            with self.assertRaises(ValueError):
                runner._budget([scenario()], policies)

    def test_feedback_dispatches_reserved_before_send_and_capped_at_three(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / 'ledger.sqlite3', {'W': 3, 'total': 3})
            for i in range(3):
                dispatch = ledger.reserve('case', 'W', 'turn')
                self.assertTrue(ledger.stopped())
                ledger.received(dispatch, 200, {})
            with self.assertRaises(RuntimeError):
                ledger.reserve('case', 'W', 'turn')
            self.assertEqual(len(ledger.dispatches()), 3)

    def test_selected_policies_execute_and_score_complete_denominators(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            suite = root / 'suite.json'
            suite.write_text(json.dumps([scenario().model_dump()]))
            out = root / 'run'
            runner.prepare(out, suite, synthetic=True, policies=('G', 'W'))
            calls = []
            def worker(out, item, policy):
                calls.append(policy)
                observed = result()
                observed.policy = policy
                return observed
            report = runner.execute(out, synthetic_only=True, worker=worker)
            self.assertEqual(calls, ['G', 'W'])
            self.assertEqual(set(report['by_policy']), {'G', 'W'})
            self.assertEqual(len(report['scores']), 2)
            self.assertEqual(report['dispatched'], 0)
            runner.execute(out, synthetic_only=True, resume=True, worker=worker)
            self.assertEqual(calls, ['G', 'W'])
