"""Disagreements and reference integrity; no OCR models required."""
import copy
import unittest

from tcm.pharmacopoeia_comparison import attach_visual_drafts, build_comparison, compare_field, field_diff, review_template, validate_page
from tcm.pharmacopoeia import MISSING_MESSAGE


def value(*texts):
    return {'occurrences': [{'raw_text': text, 'evidence': []} for text in texts]}


def fixture():
    source = {'sha256': 'a'*64, 'printed_pages': {'1': 3}}
    case = {'id': 'Caoyao', 'name_zh_candidate': '草药', 'etcm_source_record_id': 'source-1',
            'mapping_status': 'candidate', 'expected_fragments': {'usage': '不得使用这个预期生成答案'},
            'segments': [{'document': 'full', 'pdf_page': 1, 'column': 'left', 'y_range': [0, 1]}]}
    texts = ['草药', 'Caoyao', 'HERBA TEST', '本品为草药的干燥全草。', '【性状】其他内容。',
             '【性味与归经】辛，温。', '【功能与主治】原始错字。', '【用法与用量】3～6g。', '【贮藏】原文。']
    lines = [{'text': text, 'score': .99, 'pdf_box': [[5,i*5+1],[45,i*5+1],[45,i*5+3],[5,i*5+3]]}
             for i, text in enumerate(texts)]
    page = {'document': 'full', 'pdf_page': 1, 'source_sha256': 'a'*64, 'image_sha256': 'b'*64,
            'result_sha256': 'c'*64, 'pdf_width': 100, 'pdf_height': 100, 'lines': lines}
    return {'documents': {'full': source}, 'cases': [case]}, {'rapidocr': {'full-1': page}, 'paddle': {'full-1': copy.deepcopy(page)}}


class ComparisonTests(unittest.TestCase):
    def test_normalization_preserves_negation_numbers_case_and_punctuation(self):
        self.assertEqual(compare_field(value('3～6 g'), value('3~6g')), 'normalized_agreement')
        for left, right in [('禁用', '可用'), ('3g', '6g'), ('muscari', 'mucari'), ('ABC', 'abc'), ('原文。', '原文')]:
            self.assertEqual(compare_field(value(left), value(right)), 'text_difference')

    def test_missing_and_repeated_values_are_not_agreement(self):
        self.assertEqual(compare_field(value(), value()), 'both_unavailable')
        self.assertEqual(compare_field(value('注意'), value()), 'presence_difference')
        self.assertEqual(compare_field(value('甲', '乙'), value('甲')), 'occurrence_count_difference')

    def test_raw_diff_preserves_html_and_whole_text(self):
        a, b = '<img onerror=x>疮肿毒', '<img onerror=x>疮疖肿毒'
        diff = field_diff(value(a), value(b))
        self.assertEqual(''.join(p['text'] for p in diff['rapidocr']), a)
        self.assertEqual(''.join(p['text'] for p in diff['paddle']), b)
        self.assertIn('疖', ''.join(p['text'] for p in diff['paddle'] if p['changed']))

    def test_extractor_ignores_expected_answers_and_does_not_choose_winner(self):
        suite, engines = fixture()
        engines['paddle']['full-1']['lines'][7]['text'] = '【用法与用量】3～8g。'
        data = build_comparison(suite, engines)
        fields = {f['key']: f for f in data['records'][0]['fields']}
        self.assertEqual(fields['usage']['comparison'], 'text_difference')
        self.assertIn('3～6g', fields['usage']['engines']['rapidocr']['occurrences'][0]['raw_text'])
        self.assertIn('3～8g', fields['usage']['engines']['paddle']['occurrences'][0]['raw_text'])
        self.assertIsNone(fields['usage']['reference_text'])
        self.assertFalse(data['human_signoff'])

    def test_blank_references_and_missing_caution_semantics(self):
        suite, engines = fixture()
        data = build_comparison(suite, engines)
        data['artifact_sha256'] = 'd'*64
        template = review_template(data)
        self.assertEqual(len(template['fields']), 9)
        self.assertTrue(all(f['reference_text'] is None and f['reviewer'] is None for f in template['fields']))
        cautions = next(f for f in data['records'][0]['fields'] if f['key'] == 'cautions')
        self.assertEqual(cautions['comparison'], 'both_unavailable')
        self.assertEqual(cautions['engines']['paddle']['message'], MISSING_MESSAGE)

    def test_source_geometry_nan_and_bounds_rejected(self):
        _, engines = fixture()
        page = engines['rapidocr']['full-1']; expected = copy.deepcopy(page)
        for key, bad in [('image_sha256', 'wrong'), ('source_sha256', 'wrong')]:
            changed = copy.deepcopy(page); changed[key] = bad
            with self.assertRaises(ValueError): validate_page(changed, expected)
        for x in [float('nan'), -1, 101]:
            changed = copy.deepcopy(page); changed['lines'][0]['pdf_box'][0][0] = x
            with self.assertRaises(ValueError): validate_page(changed, expected)

    def test_neighbor_column_does_not_supply_missing_field(self):
        suite, engines = fixture()
        for pages in engines.values():
            pages['full-1']['lines'].append({'text': '【注意】别的药的注意项。', 'score': .99,
                                           'pdf_box': [[60, 1], [90, 1], [90, 3], [60, 3]]})
        result = build_comparison(suite, engines)
        cautions = next(f for f in result['records'][0]['fields'] if f['key'] == 'cautions')
        self.assertEqual(cautions['comparison'], 'both_unavailable')

    def test_footer_removed_with_evidence_but_dose_and_body_number_preserved(self):
        suite, engines = fixture()
        for pages in engines.values():
            for text, y in [('·3·', 95), ('3', 95), ('3g', 95), ('·4·', 95), ('·3·', 80)]:
                pages['full-1']['lines'].append({'text': text, 'score': .99,
                    'pdf_box': [[5, y], [30, y], [30, y+1], [5, y+1]]})
        result = build_comparison(suite, engines)
        record = result['records'][0]
        for engine in ['rapidocr', 'paddle']:
            self.assertEqual(len(record['excluded_margin_lines'][engine]), 1)
            self.assertEqual(record['excluded_margin_lines'][engine][0]['evidence']['text'], '·3·')
            storage = next(f for f in record['fields'] if f['key'] == 'storage')['engines'][engine]
            raw = storage['occurrences'][0]['raw_text']
            self.assertIn('3g', raw); self.assertIn('·4·', raw); self.assertIn('·3·', raw)

    def draft_fixture(self):
        suite, engines = fixture()
        engines['paddle']['full-1']['lines'][7]['text'] = '【用法与用量】3～8g。'
        data = build_comparison(suite, engines)
        field = next(f for f in data['records'][0]['fields'] if f['key'] == 'usage')
        field['previews'] = [{'image': 'assets/test.png', 'source_sha256': 'source'}]
        proposal = {'base_comparison_sha256': 'artifact', 'actor_type': 'ai', 'human_signoff': False,
                    'drafts': [{'case_id': 'Caoyao', 'field': 'usage', 'draft_text': '草案仍待审。',
                                'notes': ['对照原图后提出的AI草案。'], 'sources': [
                                    {'image': 'assets/test.png', 'source_sha256': 'source', 'image_sha256': 'image'}]}]}
        return data, proposal

    def test_ai_draft_does_not_change_ocr_or_human_references(self):
        data, proposal = self.draft_fixture()
        snapshot = copy.deepcopy(data)
        result = attach_visual_drafts(data, proposal, 'artifact', {'assets/test.png': 'image'})
        self.assertEqual(data, snapshot)
        for before, after in zip(data['records'][0]['fields'], result['records'][0]['fields']):
            self.assertEqual(before['engines'], after['engines'])
            self.assertIsNone(after['reference_text'])
            self.assertEqual(after['human_review'], 'pending')
        self.assertEqual(result['visual_draft_provenance']['approved_fields'], 0)

    def test_stale_draft_artifact_or_source_image_is_rejected(self):
        data, proposal = self.draft_fixture()
        with self.assertRaisesRegex(ValueError, 'stale'):
            attach_visual_drafts(data, proposal, 'changed-artifact', {'assets/test.png': 'image'})
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            attach_visual_drafts(data, proposal, 'artifact', {'assets/test.png': 'changed-image'})
        proposal['drafts'][0]['sources'][0]['source_sha256'] = 'different-pdf'
        with self.assertRaisesRegex(ValueError, 'source set'):
            attach_visual_drafts(data, proposal, 'artifact', {'assets/test.png': 'image'})

    def test_ai_signoff_claim_and_overwrite_of_human_reference_rejected(self):
        data, proposal = self.draft_fixture()
        proposal['human_signoff'] = True
        with self.assertRaisesRegex(ValueError, 'human signoff'):
            attach_visual_drafts(data, proposal, 'artifact', {'assets/test.png': 'image'})
        proposal['human_signoff'] = False
        data['records'][0]['fields'][0]['reference_text'] = '已有人工文本'
        with self.assertRaisesRegex(ValueError, 'human reference'):
            attach_visual_drafts(data, proposal, 'artifact', {'assets/test.png': 'image'})

    def test_duplicate_and_missing_disagreement_drafts_rejected(self):
        data, proposal = self.draft_fixture()
        proposal['drafts'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            attach_visual_drafts(data, proposal, 'artifact', {'assets/test.png': 'image'})
        proposal['drafts'] = []
        with self.assertRaisesRegex(ValueError, 'coverage'):
            attach_visual_drafts(data, proposal, 'artifact', {'assets/test.png': 'image'})

    def full_draft_fixture(self):
        suite, engines = fixture()
        data = build_comparison(suite, engines)
        record = data['records'][0]
        record['region_previews'] = [{'image': 'assets/region.png', 'source_sha256': 'source'}]
        hashes = {'assets/region.png': 'region-image'}
        drafts = []
        for field in record['fields']:
            if field['comparison'] == 'both_unavailable': continue
            image = f'assets/{field["key"]}.png'
            field['previews'] = [{'image': image, 'source_sha256': 'source'}]
            hashes[image] = 'field-image'
            drafts.append({'case_id': 'Caoyao', 'field': field['key'], 'draft_text': 'AI读图草案',
                           'notes': [], 'sources': [{'image': image, 'source_sha256': 'source', 'image_sha256': 'field-image'}]})
        proposal = {'base_comparison_sha256': 'artifact', 'actor_type': 'ai', 'human_signoff': False,
                    'scope': 'all_available_fields', 'drafts': drafts, 'missing_observations': [
                        {'case_id': 'Caoyao', 'field': 'cautions', 'finding': 'not_observed_in_selected_region',
                         'sources': [{'image': 'assets/region.png', 'source_sha256': 'source', 'image_sha256': 'region-image'}]}]}
        return data, proposal, hashes

    def test_all_fields_accepts_agreements_without_promoting_ai_to_reference(self):
        data, proposal, hashes = self.full_draft_fixture()
        result = attach_visual_drafts(data, proposal, 'artifact', hashes)
        self.assertEqual(result['visual_draft_provenance']['draft_fields'], 8)
        self.assertEqual(result['visual_draft_provenance']['missing_observations'], 1)
        cautions = next(f for f in result['records'][0]['fields'] if f['key'] == 'cautions')
        self.assertNotIn('ai_visual_draft', cautions)
        self.assertEqual(cautions['ai_visual_observation']['finding'], 'not_observed_in_selected_region')
        result['artifact_sha256'] = 'new-artifact'
        self.assertTrue(all(f['reference_text'] is None for f in review_template(result)['fields']))

    def test_full_scope_requires_both_transcriptions_and_missing_observation_coverage(self):
        data, proposal, hashes = self.full_draft_fixture()
        incomplete = copy.deepcopy(proposal); incomplete['drafts'].pop()
        with self.assertRaisesRegex(ValueError, 'coverage'):
            attach_visual_drafts(data, incomplete, 'artifact', hashes)
        proposal['missing_observations'] = []
        with self.assertRaisesRegex(ValueError, 'coverage'):
            attach_visual_drafts(data, proposal, 'artifact', hashes)

    def test_missing_observation_cannot_claim_no_contraindications_or_use_wrong_image(self):
        data, proposal, hashes = self.full_draft_fixture()
        wrong = copy.deepcopy(proposal)
        wrong['missing_observations'][0]['finding'] = 'no_contraindications'
        with self.assertRaisesRegex(ValueError, 'cannot assert'):
            attach_visual_drafts(data, wrong, 'artifact', hashes)
        wrong = copy.deepcopy(proposal)
        wrong['missing_observations'][0]['draft_text'] = '无禁忌'
        with self.assertRaisesRegex(ValueError, 'cannot assert'):
            attach_visual_drafts(data, wrong, 'artifact', hashes)
        wrong = copy.deepcopy(proposal)
        wrong['missing_observations'][0]['sources'][0]['image_sha256'] = 'wrong-region'
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            attach_visual_drafts(data, wrong, 'artifact', hashes)


if __name__ == '__main__':
    unittest.main()
