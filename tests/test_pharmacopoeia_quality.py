"""Research gating, sampling and read-only retrieval safety properties."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_pharmacopoeia_comparison import fixture
from tcm.pharmacopoeia_comparison import build_comparison
from tcm.pharmacopoeia_quality import evaluate
from tcm.pharmacopoeia_research import build_index, search_index

ROOT = Path(__file__).resolve().parents[1]


def sample():
    suite, engines = fixture()
    data = build_comparison(suite, engines)
    data['inputs'] = {engine: {'page_results': {'full-1': 'c'*64}} for engine in engines}
    registry = {'full-1': {'source_sha256': 'a'*64, 'image_sha256': 'b'*64, 'pdf_width': 100, 'pdf_height': 100}}
    policy = json.loads((ROOT/'eval/pharmacopoeia_qc_policy_v1.json').read_text())
    return data, registry, policy


def field(data, name):
    return next(f for f in data['records'][0]['fields'] if f['key'] == name)


class QualityTests(unittest.TestCase):
    def test_machine_pass_and_missing_never_become_human_approval(self):
        data, registry, policy = sample()
        original = copy.deepcopy(data)
        batch = evaluate(data, registry, policy)
        self.assertEqual(data, original)
        self.assertEqual(batch['summary']['machine_status_counts'], {'machine_checks_passed': 8, 'not_available': 1})
        self.assertTrue(all(f['human_review'] == 'pending' and not f['human_signoff'] for f in batch['fields']))
        missing = next(f for f in batch['fields'] if f['field'] == 'cautions')
        self.assertFalse(missing['eligible_for_research_preview']); self.assertIsNone(missing['excerpt'])

    def test_numeric_or_caution_disagreement_is_held_at_high_priority(self):
        data, registry, policy = sample()
        field(data, 'usage')['engines']['paddle']['occurrences'][0]['raw_text'] = '3～8g。'
        batch = evaluate(data, registry, policy)
        row = next(f for f in batch['fields'] if f['field'] == 'usage')
        self.assertEqual(row['review_priority'], 'P0'); self.assertFalse(row['eligible_for_research_preview'])
        self.assertIn('numeric_disagreement', row['reasons'])

    def test_only_one_terminal_chinese_period_can_bypass_disagreement(self):
        data, registry, policy = sample()
        field(data, 'properties_meridians')['engines']['paddle']['occurrences'][0]['raw_text'] = '辛，温'
        batch = evaluate(data, registry, policy)
        row = next(f for f in batch['fields'] if f['field'] == 'properties_meridians')
        self.assertTrue(row['eligible_for_research_preview'])
        self.assertIn('single_terminal_full_stop_difference', row['reasons'])
        field(data, 'properties_meridians')['engines']['paddle']['occurrences'][0]['raw_text'] = '辛温。'
        row = next(f for f in evaluate(data, registry, policy)['fields'] if f['field'] == 'properties_meridians')
        self.assertFalse(row['eligible_for_research_preview'])

    def test_fingerprint_and_geometry_errors_block_even_agreed_text(self):
        for mutation in ('source', 'geometry'):
            data, registry, policy = sample()
            line = field(data, 'origin')['engines']['rapidocr']['occurrences'][0]['evidence'][0]
            if mutation == 'source': line['source_sha256'] = 'wrong'
            else: line['pdf_box'][0][0] = float('nan')
            row = next(f for f in evaluate(data, registry, policy)['fields'] if f['field'] == 'origin')
            self.assertEqual(row['machine_status'], 'blocked'); self.assertFalse(row['eligible_for_research_preview'])

    def test_homoglyph_and_low_score_are_not_treated_as_agreement_proof(self):
        data, registry, policy = sample()
        for value in field(data, 'origin')['engines'].values():
            value['occurrences'][0]['raw_text'] = '本品为Erγcibe的干燥藤茎。'
        row = next(f for f in evaluate(data, registry, policy)['fields'] if f['field'] == 'origin')
        self.assertFalse(row['eligible_for_research_preview'])
        self.assertIn('mixed_script_in_latin_word', row['reasons'])
        line = field(data, 'pinyin')['engines']['rapidocr']['occurrences'][0]['evidence'][0]
        line['score'] = .3
        row = next(f for f in evaluate(data, registry, policy)['fields'] if f['field'] == 'pinyin')
        self.assertIn('low_model_confidence', row['reasons'])

    def test_sampling_reproducible_and_paused_stratum_cannot_enter_index(self):
        data, registry, policy = sample()
        a = evaluate(data, registry, policy); b = evaluate(data, registry, policy)
        self.assertEqual(a['stratified_samples'], b['stratified_samples'])
        self.assertEqual(len(a['stratified_samples']), 3)
        policy['suspended_strata'] = ['a'*64+'|identity']
        batch = evaluate(data, registry, policy)
        self.assertTrue(all(not f['eligible_for_research_preview'] for f in batch['fields'] if f['sampling_group'] == 'identity'))
        self.assertTrue(any(f['eligible_for_research_preview'] for f in batch['fields'] if f['sampling_group'] == 'description'))

    def test_ai_drafts_are_not_used_to_resolve_ocr_disagreement(self):
        data, registry, policy = sample()
        target = field(data, 'origin')
        target['engines']['paddle']['occurrences'][0]['raw_text'] += '错误'
        target['ai_visual_draft'] = {'draft_text': target['engines']['rapidocr']['occurrences'][0]['raw_text']}
        row = next(f for f in evaluate(data, registry, policy)['fields'] if f['field'] == 'origin')
        self.assertEqual(row['machine_status'], 'review_required')

    def test_fts_short_queries_and_held_fields_not_returned(self):
        data, registry, policy = sample()
        field(data, 'origin')['engines']['paddle']['occurrences'][0]['raw_text'] += '错误'
        batch = evaluate(data, registry, policy)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'research.sqlite3'
            build_index(path, batch, 'batch-1')
            self.assertEqual(search_index(path, query='原始错字')['total'], 1)
            self.assertEqual(search_index(path, query='辛')['query_mode'], 'short_query_substring')
            self.assertEqual(search_index(path, herb='Caoyao', field='origin')['total'], 0)
            self.assertEqual(search_index(path, query='草药')['query_mode'], 'exact_herb_filter')
            self.assertEqual(search_index(path, query='caoyao')['total'], 7)
            self.assertEqual(search_index(path, query='" OR 1=1 --')['total'], 0)
            with self.assertRaises(ValueError): search_index(path, field='unknown')
            with self.assertRaises(ValueError): search_index(path, query='x', limit=100)
            with self.assertRaises(FileExistsError): build_index(path, batch, 'batch-2')


if __name__ == '__main__': unittest.main()
