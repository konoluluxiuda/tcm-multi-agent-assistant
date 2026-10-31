from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import sqlite3

from tests.rag_release_fixture import fixture, save
from tcm.mineru_revisions import fingerprint
from tcm.rag_benchmark import RawCorpus, candidates, check_references, freeze, index_mapping, inside, pointer, read


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.c=fixture(self.root/'source')
        self.raw=RawCorpus(self.c['base'],self.c['source_root'],{'high':self.c['pdf_high'],'full':self.c['pdf_full']})
        self.seeds=read(self.c['seeds'])

    def test_candidates_are_not_approved_answers_and_freeze_uses_explicit_sources(self):
        rows=candidates(self.raw)
        self.assertTrue(rows)
        self.assertTrue(all(r['status']=='candidate_pending_review' and not r['usable_as_gold'] for r in rows))
        ref=freeze(self.seeds,self.raw)
        self.assertEqual(check_references(ref,self.raw)['cases'],3)
        self.assertIsNone(check_references(ref,self.raw)['retrieval_or_answer_scores'])

    def test_original_span_hash_owner_and_offsets_reject_tampering(self):
        for update in ({'text_sha256':'0'*64},{'block_sha256':'0'*64},{'entry_id':'e-beta'},{'end':999}):
            seed=deepcopy(self.seeds);seed['cases'][0]['relevant_anchors'][0].update(update)
            with self.subTest(update=update),self.assertRaises(ValueError):freeze(seed,self.raw)

    def test_unreviewed_questions_and_group_leakage_cannot_be_frozen(self):
        seed=deepcopy(self.seeds);seed['cases'][0]['review_status']='candidate_pending_visual_review'
        with self.assertRaises(ValueError):freeze(seed,self.raw)
        seed=deepcopy(self.seeds);seed['cases'][0]['split']='reserved'
        with self.assertRaisesRegex(ValueError,'leakage'):freeze(seed,self.raw)

    def test_missing_field_is_scanned_from_raw_interval_not_index(self):
        with sqlite3.connect(Path(self.c['index'])/'research.sqlite3') as db:
            db.execute("UPDATE chunks SET label='注意' WHERE label='贮藏'")
        ref=freeze(self.seeds,self.raw)
        self.assertEqual(ref['cases'][1]['expected_status'],'missing_field')
        self.raw.ordered[1]['raw']['text']='【注意】实际存在'
        self.seeds['cases'][1]['absence_witness']['interval_sha256']=fingerprint(self.raw.ordered[:3])
        with self.assertRaisesRegex(ValueError,'occurs'):self.raw.absence(self.seeds['cases'][1]['absence_witness'])

    def test_chunk_renumbering_changes_mapping_not_frozen_answer(self):
        ref=freeze(self.seeds,self.raw);before=fingerprint(ref)
        first=index_mapping(ref,self.c['index'])
        with sqlite3.connect(Path(self.c['index'])/'research.sqlite3') as db:
            db.execute("UPDATE chunks SET chunk_id='new-'||chunk_id")
        second=index_mapping(ref,self.c['index'])
        self.assertEqual(first['mapped'],second['mapped'])
        self.assertNotEqual(first['mappings'][0]['chunk_ids'],second['mappings'][0]['chunk_ids'])
        self.assertEqual(before,fingerprint(ref))
        self.assertIsNone(second['recall'])

    def test_pointer_and_relative_file_guards(self):
        self.assertEqual(pointer({'a/b':[{'~x':3}]},'/a~1b/0/~0x'),3)
        for p in ('../outside.json','/etc/passwd'):
            with self.assertRaises(ValueError):inside(self.c['source_root'],p)
        with self.assertRaises(ValueError):pointer({'a':[1]},'/a/00')

    def test_pdf_anchor_cannot_refer_to_a_nonexistent_physical_page(self):
        from tcm.mineru_pharmacopoeia import digest
        p=Path(self.c['base'])/'blocks.json';blocks=read(p)
        blocks[0]['source']['source_pdf_page']=999;save(p,blocks)
        receipt=read(Path(self.c['base'])/'summary.json');receipt['blocks_sha256']=digest(p)
        save(Path(self.c['base'])/'summary.json',receipt)
        with self.assertRaisesRegex(ValueError,'page/geometry'):
            RawCorpus(self.c['base'],self.c['source_root'],{'high':self.c['pdf_high'],'full':self.c['pdf_full']})


if __name__=='__main__':unittest.main()
