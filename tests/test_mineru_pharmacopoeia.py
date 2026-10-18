"""Boundary and evidence regressions using synthetic text, not pharmacopoeia excerpts."""
import unittest

from tcm.mineru_pharmacopoeia import normalize_blocks, segment_blocks


def raw(text, y, level=None, column='left', page=0, kind='text'):
    x = 100 if column == 'left' else 600
    value = {'type': kind, 'text': text, 'page_idx': page, 'bbox': [x, y, x + 250, y + 15]}
    if level:
        value['text_level'] = level
    return value


def normalize(items, part='part-a', first=1, count=1, document='high'):
    rows = {i: {'chapter_pdf_page': first + i, 'source_document': document,
                'source_pdf_page': first + i + 23, 'source_sha256': 'a' * 64,
                'expected_printed_page': first + i + 2, 'observed_printed_page': None}
            for i in range(count)}
    return normalize_blocks(items, part, rows, 'b' * 64)


class MineruAdapterTests(unittest.TestCase):
    def test_reorders_scrambled_columns_and_preserves_raw_indices(self):
        items = [raw('【贮藏】示例', 100, column='right'),
                 raw('本品为测试来源。', 180), raw('HERBA EXEMPLI', 150, level=2),
                 raw('示例药', 100, level=2), raw('Shilíyao', 125)]
        blocks = normalize(items)
        result = segment_blocks(blocks)
        entry = result['entries'][0]
        self.assertEqual(entry['name_zh_candidate'], '示例药')
        self.assertEqual(entry['title_block_ids'], ['part-a:3', 'part-a:4', 'part-a:2'])
        self.assertEqual(entry['flags'], ['terminal_boundary_not_independently_verified'])
        self.assertEqual([f['label'] for f in entry['fields']], ['origin', '贮藏'])

    def test_cross_part_and_source_switch_uses_chapter_continuity(self):
        a = normalize([raw('示例药 Shiliyao HERBA EXEMPLI', 100, level=2),
                       raw('本品为测试来源的', 130)])
        b = normalize([raw('续文。', 100), raw('另一药 Lingyiyao RADIX EXEMPLI', 200, level=2),
                       raw('本品以测试成分制成。', 260)], 'part-b', first=2, document='full')
        result = segment_blocks(a + b)
        self.assertEqual(len(result['entries']), 2)
        first = result['entries'][0]
        self.assertEqual(first['parts'], ['part-a', 'part-b'])
        self.assertEqual(first['chapter_pages'], [1, 2])
        self.assertEqual(first['end_boundary'], 'next_title')
        self.assertEqual(len(first['fields'][0]['segments']), 2)

    def test_missing_latin_and_pinyin_still_stops_preceding_entry(self):
        b = normalize([raw('示例药 Shiliyao HERBA EXEMPLI', 100, level=2),
                       raw('本品为测试。', 140), raw('另一药', 200, level=2),
                       raw('本品系测试。', 250)])
        result = segment_blocks(b)
        self.assertEqual(len(result['entries']), 2)
        self.assertIn('latin_title_not_observed', result['entries'][1]['flags'])
        self.assertIn('pinyin_title_not_observed', result['entries'][1]['flags'])

    def test_auxiliary_components_are_not_independent_herbs(self):
        b = normalize([raw('示例药 Shiliyao HERBA EXEMPLI', 100, level=2),
                       raw('本品为测试。', 130), raw('饮片', 170, level=2),
                       raw('【炮制】示例。', 200), raw('附：1. 示例质量标准', 250, level=2),
                       raw('示例成分', 280, level=2), raw('本品为示例成分。', 310),
                       raw('另一药 Lingyiyao RADIX EXEMPLI', 400, level=2)])
        result = segment_blocks(b)
        self.assertEqual(len(result['entries']), 2)
        fields = result['entries'][0]['fields']
        self.assertEqual(fields[1]['scope'], 'prepared_slices')
        self.assertTrue(fields[-1]['scope'].startswith('appendix:'))

    def test_field_offsets_preserve_multiple_labels_and_numeric_text(self):
        text = '【功能与主治】测试。【用法与用量】0.03～0.1g。'
        b = normalize([raw('示例药 Shiliyao HERBA EXEMPLI', 100, level=2), raw(text, 180)])
        e = segment_blocks(b)['entries'][0]
        values = [text[s['start']:s['end']] for f in e['fields'] for s in f['segments']]
        self.assertEqual(''.join(values), text)
        self.assertEqual([f['label'] for f in e['fields']], ['功能与主治', '用法与用量'])

    def test_probable_title_typo_is_flagged_without_correction(self):
        b = normalize([raw('示例叶 Shiliye FOLIUM EXEMPLI', 100, level=2),
                       raw('本品为某科植物示异叶 Exemplum plantus 的干燥叶。', 150)])
        e = segment_blocks(b)['entries'][0]
        self.assertEqual(e['name_zh_candidate'], '示例叶')
        self.assertIn('title_source_near_match_requires_review', e['flags'])

    def test_measurement_sentence_is_not_a_new_origin(self):
        b = normalize([raw('示例药 Shiliyao HERBA EXEMPLI', 100, level=2),
                       raw('本品为测试来源。', 140), raw('【含量测定】示例。', 200),
                       raw('本品以干燥品计算，含量不得少于0.070%。', 250)])
        e = segment_blocks(b)['entries'][0]
        self.assertEqual([f['label'] for f in e['fields']], ['origin', '含量测定'])
        self.assertEqual(len(e['fields'][1]['segments']), 2)

    def test_plant_and_drug_names_may_differ_legitimately(self):
        b = normalize([raw('示例叶 Shiliye FOLIUM EXEMPLI', 100, level=2),
                       raw('本品为某科植物示例 Exemplum plantus 的干燥叶。', 150)])
        self.assertNotIn('title_source_near_match_requires_review', segment_blocks(b)['entries'][0]['flags'])

    def test_margins_and_orphans_have_explicit_receipts(self):
        b = normalize([raw('前页续文', 90), raw('页眉', 50, kind='header'),
                       raw('示例药 Shiliyao HERBA EXEMPLI', 200, level=2)])
        result = segment_blocks(b)
        self.assertEqual(result['orphan_block_ids'], ['part-a:0'])
        self.assertEqual(result['margin_block_ids'], ['part-a:1'])
        self.assertEqual(result['metrics']['assigned_blocks'], 1)

    def test_invalid_geometry_and_missing_pages_are_rejected(self):
        for box in ([100, float('nan'), 300, 120], [300, 100, 200, 120], [-1, 100, 300, 120]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                normalize([{**raw('test', 100), 'bbox': box}])
        with self.assertRaises(ValueError):
            normalize([raw('test', 100)], count=2)

    def test_wide_table_precedes_both_columns_below_it(self):
        table = {**raw('', 100, kind='table'), 'bbox': [70, 100, 930, 200]}
        blocks = normalize([raw('【用法与用量】测试', 700), table,
                            raw('另一药 Lingyiyao RADIX EXEMPLI', 300, level=2, column='right'),
                            raw('测试续文', 250)])
        self.assertEqual([b['raw_block_index'] for b in blocks], [1, 3, 0, 2])
        self.assertEqual(blocks[0]['reading_order_policy'], 'full_width_table_separates_column_bands')

    def test_overlap_of_wide_table_is_explicitly_unresolved(self):
        table = {**raw('', 100, kind='table'), 'bbox': [70, 100, 930, 200]}
        blocks = normalize([table, raw('重叠正文', 170)])
        self.assertTrue(all('wide_table_overlap_order_unresolved' in b['flags'] for b in blocks))

    def test_caption_body_contamination_and_footer_image(self):
        chart = {**raw('', 200, kind='chart'), 'chart_caption': ['【检查】示例'], 'img_path': 'images/a.jpg'}
        image = {**raw('', 950, kind='image'), 'img_path': 'images/b.jpg'}
        result = segment_blocks(normalize([raw('示例药 Shiliyao HERBA EXEMPLI', 100, level=2), chart, image]))
        self.assertIn('figure_caption_contains_field_heading', result['entries'][0]['flags'])
        self.assertEqual(len(result['field_exclusions']), 1)
        self.assertEqual(len(result['entries'][0]['block_ids']), 3)


if __name__ == '__main__':
    unittest.main()
