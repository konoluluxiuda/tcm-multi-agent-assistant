"""Evaluation safeguards independent of optional OCR installations."""
import unittest
import json
from pathlib import Path
import tempfile

from scripts.benchmark_pharmacopoeia_pdf import fragment_checks, offline
from scripts.compare_pharmacopoeia_pdf import summarize
from scripts.benchmark_pharmacopoeia_pdf import SUITE
from scripts.pilot_pharmacopoeia_ocr import digest


class BenchmarkSafeguards(unittest.TestCase):
    def test_missing_continuation_is_not_run(self):
        reference = {'cases': [{'id': 'cross-page', 'segments': [
            {'document': 'full', 'pdf_page': 1}, {'document': 'full', 'pdf_page': 2}]}]}
        self.assertEqual(fragment_checks({'full-1': {}}, reference),
                         [{'id': 'cross-page', 'status': 'not_run'}])

    def test_coordinates_exclude_neighbor_and_sort_only_for_fragment_checks(self):
        def line(text, x, y):
            return {'text': text, 'pdf_box': [[x, y], [x+5, y], [x+5, y+2], [x, y+2]]}
        page = {'pdf_width': 100, 'pdf_height': 100,
                'lines': [line('乙', 10, 30), line('甲', 10, 20), line('禁用', 70, 20), line('邻药', 10, 80)]}
        reference = {'cases': [{'id': 'region', 'segments': [
            {'document': 'full', 'pdf_page': 1, 'column': 'left', 'y_range': [.1, .5]}],
            'expected_fragments': {'ordered': '甲乙', 'other_column': '禁用', 'other_entry': '邻药'}}]}
        fields = fragment_checks({'full-1': page}, reference)[0]['fields']
        self.assertEqual(fields, {'ordered': True, 'other_column': False, 'other_entry': False})
        self.assertEqual(page['lines'][0]['text'], '乙')

    def test_cross_page_fragments_and_missing_negation(self):
        reference = {'cases': [{'id': 'continuation', 'segments': [
            {'document': 'full', 'pdf_page': i, 'column': 'left', 'y_range': [0, 1]} for i in [1, 2]],
            'expected_fragments': {'complete': '孕妇禁用', 'negation': '不得内服'}}]}
        def page(text):
            return {'pdf_width': 100, 'pdf_height': 100, 'lines': [
                {'text': text, 'pdf_box': [[1, 1], [20, 1], [20, 4], [1, 4]]}]}
        result = fragment_checks({'full-1': page('孕妇'), 'full-2': page('禁用；得内服')}, reference)
        self.assertEqual(result[0]['fields'], {'complete': True, 'negation': False})

    def test_network_attempt_is_blocked_and_counted(self):
        import socket
        with offline() as attempts:
            with self.assertRaisesRegex(RuntimeError, 'Network forbidden'):
                socket.getaddrinfo('example.com', 443)
        self.assertEqual(len(attempts), 1)

    def test_failed_or_partial_run_cannot_be_compared_as_completed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for status, completed in [('failed', 0), ('partial', 5)]:
                (path/'report.json').write_text(json.dumps({
                    'prepared_sha256': 'same-input', 'suite_sha256': digest(SUITE),
                    'status': status, 'completed_pages': completed, 'total_pages': 20}))
                with self.assertRaisesRegex(ValueError, 'requires completed runs'):
                    summarize([path])

    def test_changed_suite_is_rejected_before_result_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path/'report.json').write_text(json.dumps({
                'prepared_sha256': 'same-input', 'suite_sha256': 'old-suite'}))
            with self.assertRaisesRegex(ValueError, 'frozen suite'):
                summarize([path])

    def test_different_page_images_are_not_a_valid_comparison(self):
        specs = json.loads(SUITE.read_text())['pages']
        with tempfile.TemporaryDirectory() as directory:
            directories = [Path(directory)/name for name in ('rapidocr', 'paddle')]
            for path in directories:
                path.mkdir()
                statistics = []
                for spec in specs:
                    key = f"{spec['document']}-{spec['pdf_page']}"
                    (path/f'{key}.json').write_text(json.dumps({
                        **spec, 'image_sha256': 'image-'+key, 'source_sha256': 'source',
                        'pdf_width': 100, 'pdf_height': 100, 'lines': [], 'blocks': []}))
                    statistics.append({'key': key, 'seconds': 1, 'lines': 0, 'blocks': 0})
                (path/'report.json').write_text(json.dumps({
                    'engine': path.name, 'suite_sha256': digest(SUITE), 'prepared_sha256': 'same',
                    'status': 'completed', 'completed_pages': len(specs), 'total_pages': len(specs),
                    'code_sha256': 'runner', 'versions': {}, 'settings': {}, 'pages': statistics,
                    'legacy_fragment_checks': []}))
            self.assertTrue(summarize(directories)['input_identity_verified'])
            page_path = directories[1]/'high-24.json'
            changed = json.loads(page_path.read_text())
            changed['image_sha256'] = 'different-render'
            page_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'different page images'):
                summarize(directories)


if __name__ == '__main__':
    unittest.main()
