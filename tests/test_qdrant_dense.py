import json
from pathlib import Path
import tempfile
import unittest
from tcm.qdrant_dense import check_receipt, locked_generation, point_id


class QdrantContractTests(unittest.TestCase):
    def test_point_ids_are_stable_and_distinct(self):
        from uuid import UUID
        self.assertEqual(point_id('a'),point_id('a'))
        self.assertNotEqual(point_id('a'),point_id('b'))
        self.assertEqual(UUID(point_id('a')).version,5)

    def test_source_model_and_dimension_must_match(self):
        base=dict(index_sha256='a',model_fingerprint='b',vectors_sha256='c',items_sha256='d',dimension=512,count=2)
        receipt={**base,'format_version':1,'collection':'pharmacopoeia'}
        check_receipt(receipt,base)
        for key in base:
            with self.assertRaises(ValueError):check_receipt({**receipt,key:'changed'},base)
        with self.assertRaises(ValueError):check_receipt({**receipt,'format_version':2},base)

    def test_generation_lock_times_out_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaises(ValueError):
                with locked_generation(root):pass
            (root/'receipt.json').write_text('{}')
            with locked_generation(root):
                with self.assertRaises(TimeoutError):
                    with locked_generation(root,timeout=0):pass
            with locked_generation(root,timeout=0):pass
