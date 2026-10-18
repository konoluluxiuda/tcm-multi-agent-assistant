"""Evidence and rollback contracts for research-only revision overlays."""
from copy import deepcopy
import unittest

from tcm.mineru_pharmacopoeia import segment_blocks
from tcm.mineru_revisions import entry_identity, fingerprint, render_view, target_for
from tests.test_mineru_pharmacopoeia import normalize, raw


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.blocks = normalize([raw('示错叶 Shicuoye FOLIUM EXEMPLI', 100, level=2),
                                 raw('本品为测试来源。', 160),
                                 raw('【用法与用量】1～2g，示错词。', 220)])
        self.by_id = {b['id']: b for b in self.blocks}
        self.parsed = segment_blocks(self.blocks)
        self.entry = self.parsed['entries'][0]

    def overlay(self, proposal, number=1):
        return {'id': f'edit-{number}', 'entry_id': self.entry['id'],
                'entry_identity_sha256': fingerprint(entry_identity(self.entry)),
                'review_status': 'ai_proposed_pending_human_review', 'human_signoff': False,
                'enabled': True, 'proposal': proposal, 'target': target_for(proposal, self.entry, self.by_id),
                'evidence': {'image': 'synthetic.png'}}

    def ledger(self, overlays):
        return {'entries_sha256': 'a', 'blocks_sha256': 'b', 'human_signoff': False, 'overlays': overlays}

    def render(self, overlays):
        return render_view(self.parsed, self.by_id, self.ledger(overlays), 'a', 'b')['entries'][0]

    def name(self):
        return {'target': 'name_zh_candidate', 'observed': '示错叶', 'proposed_source_reading': '示例叶'}

    def word(self):
        return {'target': '用法与用量', 'observed': '示错词', 'proposed_source_reading': '示例词'}

    def test_name_correction_retains_raw_identity_and_pending_status(self):
        before = deepcopy(self.parsed)
        view = self.render([self.overlay(self.name())])
        self.assertEqual(view['name_zh_candidate'], '示例叶')
        self.assertEqual(view['raw_name_zh_candidate'], '示错叶')
        self.assertFalse(view['production_eligible'])
        self.assertFalse(view['human_signoff'])
        self.assertFalse(view['identity_mapping_verified'])
        self.assertEqual(view['raw_flags'], self.entry['flags'])
        self.assertEqual(self.parsed, before)

    def test_word_correction_preserves_numeric_dose_and_source_offsets(self):
        overlay = self.overlay(self.word())
        view = self.render([overlay]);field = view['fields'][1]
        self.assertEqual(field['text'], '【用法与用量】1～2g，示例词。')
        self.assertEqual(field['raw_text'], '【用法与用量】1～2g，示错词。')
        self.assertEqual(field['evidence'][0]['start'], 0)
        self.assertEqual(field['overlay_ids'], ['edit-1'])

    def test_disable_is_a_complete_reversal(self):
        overlay = self.overlay(self.word());overlay['enabled'] = False
        self.assertEqual(self.render([overlay]), self.render([]))

    def test_changed_or_ambiguous_observation_is_rejected(self):
        with self.assertRaises(ValueError):
            self.overlay({**self.name(), 'observed': '另一叶'})
        self.entry['fields'].append(deepcopy(self.entry['fields'][1]))
        with self.assertRaises(ValueError):
            self.overlay(self.word())

    def test_overlapping_revisions_and_duplicate_ids_are_rejected(self):
        first = self.overlay(self.word());second = self.overlay(self.word(), number=2)
        with self.assertRaises(ValueError):self.render([first, second])
        with self.assertRaises(ValueError):self.render([first, first])

    def test_changed_corpus_or_entry_identity_is_rejected(self):
        overlay = self.overlay(self.name())
        with self.assertRaises(ValueError):
            render_view(self.parsed, self.by_id, self.ledger([overlay]), 'changed', 'b')
        self.entry['chapter_pages'] = [99]
        with self.assertRaises(ValueError):self.render([overlay])

    def test_changed_span_target_is_rejected_even_if_text_exists(self):
        overlay = self.overlay(self.word());overlay['target']['start'] += 1
        with self.assertRaises(ValueError):self.render([overlay])

    def test_ai_overlays_cannot_claim_human_signoff(self):
        overlay = self.overlay(self.name());overlay['human_signoff'] = True
        with self.assertRaises(ValueError):self.render([overlay])

    def test_missing_title_addition_keeps_missing_flag_and_source_evidence(self):
        self.entry['title_flags'].append('pinyin_title_not_observed')
        proposal = {'target': 'omitted_title_lines', 'proposed_pinyin_reading': 'Shiliye',
                    'proposed_latin_reading': None}
        view = self.render([self.overlay(proposal)])
        self.assertEqual(view['title_additions']['values'], {'pinyin': 'Shiliye'})
        self.assertEqual(view['title_additions']['evidence'], {'image': 'synthetic.png'})
        self.entry['title_flags'].remove('pinyin_title_not_observed')
        with self.assertRaises(ValueError):self.overlay(proposal)

    def test_equation_text_and_citation_survive_view_rendering(self):
        self.blocks.append({**self.blocks[-1], 'id': 'equation',
                            'raw': {'type': 'equation', 'text': '$$a=b$$', 'bbox': [100, 250, 300, 280]}})
        self.by_id = {b['id']: b for b in self.blocks}
        self.parsed = segment_blocks(self.blocks)
        view = self.render([])
        self.assertIn('$$a=b$$', view['fields'][1]['text'])
        self.assertEqual(view['fields'][1]['evidence'][-1]['block_type'], 'equation')


if __name__ == '__main__':
    unittest.main()
