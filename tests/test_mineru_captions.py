"""Caption paragraphs keep their own geometry and their raw-string offsets."""
from copy import deepcopy
import unittest

from tcm.mineru_pharmacopoeia import attach_caption_evidence, segment_blocks, segment_text
from tcm.mineru_revisions import citation
from tests.test_mineru_pharmacopoeia import normalize, raw


def fixture():
    captions = ['图例', '【检查】测试值不得过 $10.0\\%$。', '灰分测试。']
    table_captions = ['重金属测试。', '【含量测定】测试。']
    chart = {**raw('', 200, kind='chart'), 'img_path': 'images/chart.jpg', 'chart_caption': captions}
    table = {**raw('', 600, kind='table'), 'img_path': 'images/table.jpg', 'table_caption': table_captions}
    items = [raw('示例药 Shiliyao HERBA EXEMPLI', 100, level=2), raw('本品为测试。', 150),
             raw('【特征图谱】测试。', 180), chart, table, raw('测定续文。', 650)]
    def child(kind, text, y):
        return {'type': kind, 'bbox': [100, y, 350, y + 15],
                'lines': [{'spans': [{'content': text}]}]}
    layout = {'pdf_info': [{'page_size': [1000, 1000], 'para_blocks': [
        {'type': 'chart', 'blocks': [
            {'type': 'chart_body', 'lines': [{'spans': [{'image_path': 'chart.jpg'}]}]},
            *[child('chart_caption', text.replace('$', ''), y)
              for text, y in zip(captions, [250, 300, 350])]]},
        {'type': 'table', 'blocks': [
            *[child('table_caption', text, y) for text, y in zip(table_captions, [400, 450])],
            {'type': 'table_body', 'lines': [{'spans': [{'image_path': 'table.jpg'}]}]}]}
    ]}]}
    return items, layout


class CaptionTests(unittest.TestCase):
    def test_body_caption_is_assigned_to_fields_in_geometric_order(self):
        items, layout = fixture();original = deepcopy(items)
        blocks = attach_caption_evidence(normalize(items), layout, 'layout-sha')
        by_id = {b['id']: b for b in blocks};e = segment_blocks(blocks)['entries'][0]
        self.assertEqual([f['label'] for f in e['fields']], ['origin', '特征图谱', '检查', '含量测定'])
        check = e['fields'][2]
        self.assertEqual([segment_text(s, by_id) for s in check['segments']],
                         ['【检查】测试值不得过 $10.0\\%$。', '灰分测试。', '重金属测试。'])
        measure = e['fields'][3]
        self.assertEqual(measure['segments'][1], {'block_id': 'part-a:4'})
        self.assertEqual(items, original)
        self.assertEqual(len(e['block_ids']), len(set(e['block_ids'])))

    def test_caption_citation_uses_child_not_figure_bbox(self):
        items, layout = fixture()
        blocks = attach_caption_evidence(normalize(items), layout, 'layout-sha')
        e = segment_blocks(blocks)['entries'][0]
        ref = citation(e['fields'][2]['segments'][0], {b['id']: b for b in blocks})
        self.assertEqual(ref['bbox_0_1000'], [100, 300, 350, 315])
        self.assertEqual(ref['layout_sha256'], 'layout-sha')
        self.assertEqual(ref['text_source'], 'chart_caption')
        self.assertEqual(ref['item_index'], 1)

    def test_changed_caption_text_or_count_is_rejected(self):
        for change in ('text', 'count'):
            items, layout = fixture()
            if change == 'text':items[3]['chart_caption'][0] = '另一图例'
            else:items[3]['chart_caption'].append('多出文字')
            with self.subTest(change=change), self.assertRaises(ValueError):
                attach_caption_evidence(normalize(items), layout, 'layout-sha')

    def test_invalid_caption_geometry_is_rejected(self):
        items, layout = fixture()
        layout['pdf_info'][0]['para_blocks'][0]['blocks'][1]['bbox'] = [100, -1, 350, 250]
        with self.assertRaises(ValueError):attach_caption_evidence(normalize(items), layout, 'layout-sha')


if __name__ == '__main__':
    unittest.main()
