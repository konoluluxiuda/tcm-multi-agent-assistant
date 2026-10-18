"""Missing heading metadata must not mix drugs or shift source-bound revisions."""
from copy import deepcopy
import unittest

from tcm.mineru_pharmacopoeia import segment_blocks, title_at
from tcm.mineru_revisions import rebase_entries, rebased_target
from tests.test_mineru_pharmacopoeia import normalize, raw


def corpus():
    return normalize([raw('示例药 Shiliyao HERBA EXEMPLI', 100, level=2),
                      raw('本品为测试来源。', 160), raw('【功能与主治】示例。', 200),
                      raw('另一药', 300), raw('Lingyiyao RADIX EXEMPLI', 340),
                      raw('本品为另一测试来源。', 400), raw('【功能与主治】另一示例。', 450)])


class HeadingRecoveryTests(unittest.TestCase):
    def test_unmarked_title_creates_separate_entry(self):
        entries = segment_blocks(corpus())['entries']
        self.assertEqual([e['name_zh_candidate'] for e in entries], ['示例药', '另一药'])
        self.assertEqual([len([f for f in e['fields'] if f['label'] == '功能与主治']) for e in entries], [1,1])
        self.assertIn('heading_level_missing_boundary_inferred', entries[1]['flags'])
        self.assertNotIn('part-a:3', entries[0]['block_ids'])

    def test_absent_or_remote_source_does_not_infer_heading(self):
        for change in ('absent','remote'):
            blocks = corpus()
            if change == 'absent':blocks[5]['raw']['text']='无来源佐证。'
            else:blocks[5]['raw']['bbox']=[100,650,350,665]
            with self.subTest(change=change):self.assertIsNone(title_at(blocks,3))

    def test_unaligned_or_incomplete_identity_is_rejected(self):
        for change in ('pinyin','latin','alignment'):
            blocks=corpus()
            if change=='pinyin':blocks[4]['raw']['text']='RADIX EXEMPLI'
            elif change=='latin':blocks[4]['raw']['text']='Lingyiyao'
            else:blocks[4]['raw']['bbox']=[350,340,495,355]
            with self.subTest(change=change):self.assertIsNone(title_at(blocks,3))

    def test_column_continuation_is_allowed_in_reading_order(self):
        b=normalize([raw('另一药',850),raw('Lingyiyao RADIX EXEMPLI',880),
                     raw('本品为测试来源。',100,column='right')])
        self.assertIsNotNone(title_at(b,0))

    def test_page_continuation_requires_end_and_top_positions(self):
        b=normalize([raw('另一药',850,column='right'),raw('Lingyiyao RADIX EXEMPLI',880,column='right'),
                     raw('本品为测试来源。',100,page=1)],count=2)
        self.assertIsNotNone(title_at(b,0))
        b[2]['raw']['bbox'][1]=400
        self.assertIsNone(title_at(b,0))

    def test_caption_text_cannot_be_a_title_or_source_witness(self):
        b=corpus();b[3]['parent_id']='figure'
        self.assertIsNone(title_at(b,3))
        b=corpus();b[5]['parent_id']='figure'
        self.assertIsNone(title_at(b,3))

    def test_source_ids_do_not_depend_on_preceding_entry_count(self):
        blocks=corpus();original=segment_blocks(blocks)['entries']
        without_prior=segment_blocks(blocks[3:])['entries']
        self.assertEqual(original[1]['id'],without_prior[0]['id'])
        self.assertNotEqual(original[0]['id'],original[1]['id'])

    def test_source_id_changes_if_raw_title_geometry_changes(self):
        a=corpus();b=deepcopy(a);b[3]['raw']['bbox'][0]+=1
        self.assertNotEqual(segment_blocks(a)['entries'][1]['id'],segment_blocks(b)['entries'][1]['id'])

    def test_old_ordinal_rebases_to_source_id_even_when_pages_are_trimmed(self):
        blocks=corpus();by_id={b['id']:b for b in blocks};new=segment_blocks(blocks)['entries']
        old=deepcopy(new[:1]);old[0]['id']='legacy-1';old[0]['chapter_pages']=[1,2]
        self.assertEqual(rebase_entries(old,by_id,new,by_id),{'legacy-1':new[0]['id']})

    def test_missing_or_ambiguous_anchor_is_rejected(self):
        blocks=corpus();by_id={b['id']:b for b in blocks};entries=segment_blocks(blocks)['entries']
        with self.assertRaises(ValueError):rebase_entries(entries,by_id,entries[1:],by_id)
        with self.assertRaises(ValueError):rebase_entries(entries,by_id,entries+[deepcopy(entries[0])],by_id)

    def test_span_rebase_preserves_source_and_rejects_changed_body(self):
        blocks=corpus();by_id={b['id']:b for b in blocks};entry=segment_blocks(blocks)['entries'][0]
        proposal={'target':'功能与主治','observed':'示例。','proposed_source_reading':'测试。'}
        old=deepcopy(entry);old['id']='legacy-1'
        target=rebased_target(proposal,old,by_id,entry,by_id)
        self.assertEqual(target['kind'],'replace_span')
        changed=deepcopy(by_id);changed['part-a:2']['raw']['text']+='附加文本'
        with self.assertRaises(ValueError):rebased_target(proposal,old,by_id,entry,changed)

    def test_rebase_refuses_word_that_moved_to_another_entry(self):
        blocks=corpus();by_id={b['id']:b for b in blocks};new=segment_blocks(blocks)['entries']
        old=deepcopy(new[0]);old['fields']+=deepcopy(new[1]['fields'])
        proposal={'target':'origin_text','observed':'另一测试来源','proposed_source_reading':'测试来源'}
        with self.assertRaises(ValueError):rebased_target(proposal,old,by_id,new[0],by_id)

    def test_field_index_may_change_but_source_span_must_stay_fixed(self):
        blocks=corpus();by_id={b['id']:b for b in blocks};old=segment_blocks(blocks)['entries'][0]
        new=deepcopy(old);new['fields'].insert(0,{'scope':'monograph','label':'other','segments':[]})
        proposal={'target':'功能与主治','observed':'示例。','proposed_source_reading':'测试。'}
        target=rebased_target(proposal,old,by_id,new,by_id)
        self.assertEqual(target['field_index'],2)
        self.assertEqual(target['segment']['block_id'],'part-a:2')


if __name__=='__main__':unittest.main()
