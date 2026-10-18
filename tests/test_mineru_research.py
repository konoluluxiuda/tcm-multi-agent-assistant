"""Research UI backend: source binding, read-only concurrency and bounded previews."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_mineru_retrieval import build
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_research import ResearchService, SnapshotChanged
from tests.test_mineru_retrieval import fixture

try:
    import pymupdf
    from PIL import Image
except ImportError:
    pymupdf = None


@unittest.skipUnless(pymupdf, 'Optional PDF preview dependencies required')
class ResearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'source.pdf'
        with pymupdf.open() as document:
            for color in [(1, 0, 0), (0, 1, 0), (0, 0, 1)]:
                page = document.new_page(width=200, height=300)
                page.draw_rect(page.rect, color=color, fill=color)
            document.save(self.pdf)
        fixture(self.root / 'view')
        path = self.root / 'view/research_view.json'
        view = json.loads(path.read_text())
        for entry in view['entries']:
            for citation in entry['title_evidence'] + [c for f in entry['fields'] for c in f['evidence']]:
                citation['source']['source_sha256'] = digest(self.pdf)
        path.write_text(json.dumps(view))
        receipt = self.root / 'view/summary.json'
        data = json.loads(receipt.read_text()); data['research_view_sha256'] = digest(path)
        receipt.write_text(json.dumps(data))
        build(self.root / 'view', self.root / 'index')
        self.service = ResearchService(self.root / 'index', {'test': self.pdf})
        self.hit = self.service.search('示例叶炭功效')['hits'][0]

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_uses_physical_page_and_highlights_source_box(self):
        content = self.service.preview(self.hit['chunk_id'], 'evidence', 0)
        image = Image.open(io.BytesIO(content)).convert('RGB')
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 255))  # Third page, not chapter page 1.
        self.assertEqual(image.getpixel((int(image.width * .1), int(image.height * .2))), (225, 29, 72))

    def test_title_evidence_is_also_previewable(self):
        self.assertTrue(self.service.preview(self.hit['chunk_id'], 'title_evidence', 0).startswith(b'\x89PNG'))

    def test_arbitrary_files_or_indices_are_not_evidence(self):
        for chunk, kind, index, error in [('../../source.pdf', 'evidence', 0, LookupError),
                (self.hit['chunk_id'], '../source.pdf', 0, ValueError),
                (self.hit['chunk_id'], 'evidence', -1, ValueError),
                (self.hit['chunk_id'], 'evidence', 99999, LookupError)]:
            with self.subTest(chunk=chunk, kind=kind, index=index), self.assertRaises(error):
                self.service.preview(chunk, kind, index)

    def test_concurrent_queries_are_read_only_and_thread_safe(self):
        before = digest(self.root / 'index/research.sqlite3')
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(self.service.search, ['示例叶炭功效'] * 6))
        self.assertTrue(all(r['hits'][0]['name'] == '示例叶炭' and not r['human_signoff'] for r in results))
        self.assertEqual(before, digest(self.root / 'index/research.sqlite3'))

    def test_changed_pdf_stops_search_and_rendering(self):
        self.pdf.write_bytes(self.pdf.read_bytes() + b'changed')
        with self.assertRaises(SnapshotChanged):
            self.service.search('示例叶炭功效')
        with self.assertRaises(SnapshotChanged):
            self.service.preview(self.hit['chunk_id'], 'evidence', 0)

    def test_changed_index_receipt_stops_search(self):
        path = self.root / 'index/manifest.json'; path.write_text('{}')
        with self.assertRaises(SnapshotChanged):
            self.service.search('示例叶炭功效')

    def test_wrong_source_pdf_is_rejected_on_startup(self):
        other = self.root / 'other.pdf'; other.write_bytes(b'not the cited document')
        with self.assertRaises(ValueError):
            ResearchService(self.root / 'index', {'test': other})

    def test_invalid_source_coordinates_are_rejected(self):
        for box in ([0, 0, 1001, 20], [50, 0, 10, 20], [0, 0, float('nan'), 20]):
            citation = dict(self.hit['evidence'][0], bbox_0_1000=box)
            with self.assertRaises(ValueError):
                self.service.validate_citation(citation)
