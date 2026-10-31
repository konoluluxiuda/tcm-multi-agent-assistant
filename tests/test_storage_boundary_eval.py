from copy import deepcopy
import hashlib
import json
import sqlite3
import unittest

from scripts.eval_storage_boundaries import assess_cases
from tests.test_mineru_pharmacopoeia import normalize, raw
from tcm.mineru_revisions import citation, fingerprint


class BoundaryEvaluationTests(unittest.TestCase):
    def setUp(self):
        blocks = normalize([raw('【贮藏】示例贮藏。', 100), raw('注：示例试剂配制。', 150)])
        self.blocks = {b['id']: b for b in blocks}
        self.segments = [{'block_id': b['id'], 'start': 0, 'end': len(b['raw']['text'])} for b in blocks]
        self.case = {'case_id': 'test', 'name': '合成', 'entry_id': 'entry-test', 'scope': 'monograph',
            'kind': 'post_storage_note', 'storage_segments': self.segments[:1],
            'annotation_block_ids': [blocks[1]['id']],
            'expected_storage_sha256': hashlib.sha256(blocks[0]['raw']['text'].encode()).hexdigest(),
            'source_witnesses': {b['id']: {'block_sha256': fingerprint(b), 'source': b['source'],
                               'bbox_0_1000': b['raw']['bbox']} for b in blocks}}
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE chunks(entry_id TEXT,label TEXT,scope TEXT,raw_text TEXT,payload TEXT)')

    def add(self, label, segments, *, text=None, scope='monograph'):
        if text is None: text = '\n'.join(self.blocks[s['block_id']]['raw']['text'] for s in segments)
        evidence = [citation(s, self.blocks) for s in segments]
        self.db.execute('INSERT INTO chunks VALUES(?,?,?,?,?)', ('entry-test', label, scope, text, json.dumps({'evidence': evidence})))

    def assess(self): return assess_cases(self.db, self.blocks, [self.case])[0]

    def test_reference_rejects_shared_upstream_pollution(self):
        self.add('贮藏', self.segments)
        result = self.assess()
        self.assertFalse(result['storage_value_exact']); self.assertFalse(result['storage_evidence_exact'])
        self.assertEqual(result['status'], 'failed')

    def test_exact_value_and_both_source_assignments_pass(self):
        self.add('贮藏', self.segments[:1]); self.add('条目注释', self.segments[1:])
        self.assertEqual(self.assess()['status'], 'passed')

    def test_clean_text_with_polluted_citation_or_deleted_notes_does_not_pass(self):
        self.add('贮藏', self.segments, text=self.blocks[self.segments[0]['block_id']]['raw']['text'])
        result = self.assess()
        self.assertTrue(result['storage_value_exact']); self.assertFalse(result['storage_evidence_exact'])
        self.assertFalse(result['annotations_preserved_separately'])

    def test_changed_original_block_or_reference_text_fails_closed(self):
        self.blocks[self.segments[0]['block_id']]['raw']['text'] += 'changed'
        with self.assertRaisesRegex(ValueError, 'witness'): self.assess()
        self.blocks[self.segments[0]['block_id']]['raw']['text'] = '【贮藏】示例贮藏。'
        self.case['expected_storage_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'checksum'): self.assess()

    def test_prepared_slice_cannot_substitute_for_monograph(self):
        self.add('贮藏', self.segments[:1], scope='prepared_slices')
        self.add('条目注释', self.segments[1:])
        self.assertEqual(self.assess()['status'], 'failed')


if __name__ == '__main__': unittest.main()
