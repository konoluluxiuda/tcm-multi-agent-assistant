"""Dependency upgrades must invalidate saved execution contracts before new calls."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tcm.research_run_demo import OfflineModel, synthetic_tool
from tcm.research_run_durable import run_durable, RunConflict
from tcm.runtime_versions import framework_versions


class UpgradeBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_journal_refuses_framework_change_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            async def run(**kwargs):
                return await run_durable('synthetic', OfflineModel(), synthetic_tool,
                    path=Path(directory)/'runs.sqlite3', owner='upgrade-test',
                    identifier='upgrade-test-001', contract='synthetic-v1', **kwargs)
            self.assertEqual((await run())['status'], 'complete')
            changed = {**framework_versions(), 'langgraph': 'future-incompatible'}
            with patch('tcm.runtime_versions.framework_versions', return_value=changed):
                with self.assertRaises(RunConflict):
                    await run(resume=True)

    async def test_checkpoint_fingerprint_changes_when_dependency_changes(self):
        from tcm.report_checkpoint import source_fingerprint
        with patch.dict('os.environ', {'TCM_DB': '', 'ETCM_ENRICHMENT_DB': '', 'PHARMACOPOEIA_INDEX': ''}):
            previous = source_fingerprint()
            with patch('tcm.report_checkpoint.framework_versions', return_value={'langgraph': 'changed'}):
                self.assertNotEqual(previous, source_fingerprint())


if __name__ == '__main__':
    unittest.main()
