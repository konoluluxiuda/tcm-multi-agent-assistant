"""Container snapshots include committed WAL data without relaxing source permissions."""
import json
import sqlite3
import unittest

from tests import test_herb_target_paths as fixtures
from scripts.prepare_etcm_runtime import prepare


class RuntimeSnapshotTests(unittest.TestCase):
    def setUp(self):fixtures.HerbTargetPathTests.setUp(self)

    def test_wal_snapshot_is_standalone_and_preserves_source_permissions(self):
        mode = self.enrichment.parent.stat().st_mode
        output = self.root / 'runtime'
        with sqlite3.connect(self.enrichment) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('CREATE TABLE wal_probe(value TEXT)')
            writer.execute("INSERT INTO wal_probe VALUES('committed WAL row')")
            writer.commit()
            receipt = prepare(self.enrichment, self.targets, output)
        path = output / 'catalogs/enrichment.sqlite3'
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as con:
            self.assertEqual(con.execute('SELECT value FROM wal_probe').fetchone()[0], 'committed WAL row')
            self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
        self.assertEqual(self.enrichment.parent.stat().st_mode, mode)
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o755)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertFalse(path.with_name(path.name + '-wal').exists())
        self.assertFalse(receipt['source_permissions_changed'])

    def test_existing_output_is_never_overwritten(self):
        output = self.root / 'runtime';prepare(self.enrichment, self.targets, output)
        with self.assertRaises(FileExistsError):prepare(self.enrichment, self.targets, output)

    def test_mismatched_catalog_is_rejected_before_creating_output(self):
        with sqlite3.connect(self.targets) as con:
            con.execute("UPDATE metadata SET value='\"other\"' WHERE key='herb_source_fingerprint'")
        output = self.root / 'runtime'
        with self.assertRaises(ValueError):prepare(self.enrichment, self.targets, output)
        self.assertFalse(output.exists())


if __name__ == '__main__':unittest.main()
