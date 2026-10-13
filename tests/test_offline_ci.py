"""Prove missing coverage and swallowed network failures cannot turn CI green."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.run_offline_ci import ROOT, test_environment


class OfflineRunnerTests(unittest.TestCase):
    def check_worker(self, body):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test_probe.py'
            path.write_text('import unittest\nimport socket\n' + body)
            report = Path(directory) / 'report.json'
            proc = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/run_offline_ci.py'),
                 '--worker', str(path), '--worker-output', str(report)],
                env=test_environment(), capture_output=True, text=True, timeout=20,
            )
            return proc.returncode, json.loads(report.read_text())

    def test_skip_is_failure(self):
        code, report = self.check_worker("class Probe(unittest.TestCase):\n @unittest.skip('missing dependency')\n def test_one(self): pass\n")
        self.assertEqual(code, 1)
        self.assertEqual(report['skipped'], 1)

    def test_swallowed_network_attempt_is_failure(self):
        code, report = self.check_worker("class Probe(unittest.TestCase):\n def test_one(self):\n  try: socket.create_connection(('127.0.0.1', 9))\n  except RuntimeError: pass\n")
        self.assertEqual(code, 1)
        self.assertGreater(report['network_attempts'], 0)

    def test_assertion_failure_is_failure(self):
        code, report = self.check_worker("class Probe(unittest.TestCase):\n def test_one(self): self.assertEqual(1, 2)\n")
        self.assertEqual(code, 1)
        self.assertEqual(report['failures'], 1)

    def test_real_pass_is_success(self):
        code, report = self.check_worker("class Probe(unittest.TestCase):\n def test_one(self): self.assertEqual(1, 1)\n")
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'passed')


if __name__ == '__main__':
    unittest.main()
