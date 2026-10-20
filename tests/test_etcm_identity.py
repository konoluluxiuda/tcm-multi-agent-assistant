"""Source comparisons must distinguish field agreement from verified identity."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tcm.etcm_identity import compare, latin_names, origin_parts


class IdentityTests(unittest.TestCase):
    def comparison(self, latin='Demoa exemplaris Author.', part='Root',
                   origin='本品为示例植物 Demoa exemplaris Author. 的干燥根。采收时除去根茎。',
                   missing=False, scope='monograph'):
        record = {'source_record_id': 'source-1', 'source': {'file_sha256': 'source-sha'},
                  'fields': {'latin_name': {'state': 'present', 'value': latin, 'source': {'json_pointer': '/latin'}},
                             'medicinal_part': {'state': 'present', 'value': part, 'source': {'json_pointer': '/part'}}}}
        candidate = {'name_zh': '测试草', 'entry_id': 'entry-1', 'identity_verified': False}
        block = {'id': 'origin-1', 'raw': {'text': origin}, 'source': {'source_pdf_page': 3}}
        entry = {'id': 'entry-1', 'fields': [{'scope': scope, 'label': 'origin',
                 'segments': [{'block_id': block['id'], 'start': 0, 'end': len(origin)}]}]}
        return compare(record, candidate, entry, {} if missing else {block['id']: block})

    def test_same_literals_do_not_approve_or_merge_identity(self):
        result = self.comparison()
        self.assertEqual(result['status'], 'fields_consistent_not_verified')
        self.assertFalse(result['identity_verified'])
        self.assertFalse(result['human_reviewed'])
        self.assertEqual(result['origin_evidence'][0]['source']['source_pdf_page'], 3)
        self.assertEqual(result['part_comparison']['etcm_field_source']['json_pointer'], '/part')

    def test_root_and_rhizome_difference_is_explicit(self):
        result = self.comparison(origin='本品为示例植物 Demoa exemplaris Author. 的干燥根茎。')
        self.assertEqual(result['status'], 'medicinal_part_difference')
        self.assertEqual(result['part_comparison']['etcm_original'], 'Root')
        self.assertEqual(result['part_comparison']['pharmacopoeia_parts'], ['根茎'])

    def test_harvesting_words_do_not_change_definition(self):
        self.assertEqual(origin_parts('本品的干燥根。采收后除去根茎。'), ['根'])
        self.assertIsNone(origin_parts('本品为植物的果实。采收后除去干燥根。'))

    def test_unknown_part_or_missing_origin_does_not_pass(self):
        self.assertEqual(self.comparison(part='Unknown organ')['status'], 'insufficient_evidence')
        self.assertEqual(self.comparison(missing=True)['status'], 'insufficient_evidence')
        self.assertEqual(self.comparison(scope='prepared_slices')['status'], 'insufficient_evidence')

    def test_full_taxon_runs_preserve_variety_and_author_differences(self):
        result = self.comparison(latin='Demoa exemplaris Author.',
                                origin='本品为示例植物 Demoa exemplaris Author. var. minor Other. 的干燥根。')
        self.assertEqual(result['status'], 'needs_identity_review')
        result = self.comparison(latin='Demoa exemplaris Other.')
        self.assertEqual(result['status'], 'needs_identity_review')
        self.assertEqual(len(latin_names('Demoa exemplaris A.; Demoa major B.')), 2)

    def test_multiple_taxa_and_whitespace_compare_as_sets(self):
        result = self.comparison(latin='Demoa exemplaris(A.)B.; Demoa major C.', part='Root, Rhizome',
                                origin='本品为植物 Demoa major C. 或示例植物 Demoa exemplaris (A.) B. 的干燥根和根茎。')
        self.assertEqual(result['status'], 'fields_consistent_not_verified')
        self.assertEqual(result['part_comparison']['pharmacopoeia_parts'], ['根', '根茎'])

    def test_evidence_slices_exclude_neighbor_text(self):
        record = {'source_record_id': '1', 'source': {}, 'fields': {}}
        text = '相邻片段本品的干燥根。'
        block = {'id': 'b', 'raw': {'text': text}}
        entry = {'id': 'e', 'fields': [{'scope': 'monograph', 'label': 'origin',
                 'segments': [{'block_id': 'b', 'start': 4, 'end': len(text)}]}]}
        result = compare(record, {'name_zh': '示例'}, entry, {'b': block})
        self.assertEqual(result['origin_evidence'][0]['text'], '本品的干燥根。')
        entry['fields'][0]['segments'][0]['end'] += 1
        self.assertEqual(compare(record, {'name_zh': '示例'}, entry, {'b': block})['status'], 'insufficient_evidence')

    def test_persisted_comparison_is_optional_and_source_bound(self):
        from scripts.build_etcm_enrichment import build as catalog
        from scripts.compare_etcm_identity import build
        from tcm.etcm_enrichment import enrich_records
        from tcm.herbs import import_snapshot, lookup
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            (source / 'etcm_herbs').mkdir(parents=True)
            original = {'code': 1, 'data': [{'id': 'base_information', 'value': [
                {'key': 'Herb Name in Pinyin', 'value': 'TestHerb'},
                {'key': 'Herb Name in Latin', 'value': 'Demoa exemplaris Author.'},
                {'key': 'Medicinal Part', 'value': 'Root'}]}]}
            raw = source / 'etcm_herbs/TestHerb.json'
            raw.write_text(json.dumps(original))
            base = root / 'base.sqlite3'
            import_snapshot(source, base, 'v1')
            sha_before = hashlib.sha256(base.read_bytes()).hexdigest()
            entries_path, blocks_path = root / 'entries.json', root / 'blocks.json'
            text = '本品为示例植物 Demoa exemplaris Author. 的干燥根。'
            entries = {'entries': [{'id': 'entry-1', 'name_zh_candidate': '测试草', 'title_flags': [],
                       'title_block_ids': ['title-1'], 'fields': [{'scope': 'monograph', 'label': 'origin',
                       'segments': [{'block_id': 'origin-1', 'start': 0, 'end': len(text)}]}]}]}
            blocks = [{'id': 'title-1', 'raw': {'text': 'TestHerb TEST HERB'}},
                      {'id': 'origin-1', 'raw': {'text': text}, 'source': {'source_pdf_page': 2}}]
            entries_path.write_text(json.dumps(entries)); blocks_path.write_text(json.dumps(blocks))
            derived = root / 'derived'
            catalog(source, base, 'v1', derived, entries_path, blocks_path)
            database = derived / 'enrichment.sqlite3'
            def query():
                return lookup(base, name='TestHerb', dataset_version='v1')['records'][0]
            with patch.dict(os.environ, {'ETCM_ENRICHMENT_DB': str(database)}):
                self.assertNotIn('identity_comparisons', enrich_records([query()], 'v1')[0])
                report = build(database, entries_path, blocks_path, root / 'report.json', ['测试草'])
                self.assertEqual(report['count'], 1)
                enriched = enrich_records([query()], 'v1')[0]
                self.assertEqual(len(enriched['identity_comparisons']), 1)
                self.assertEqual(enriched['identity_comparisons'][0]['status'], 'fields_consistent_not_verified')
                self.assertFalse(enriched['identity_comparisons'][0]['identity_verified'])
                changed = query(); changed['source']['file_sha256'] = 'different'
                self.assertNotIn('identity_comparisons', enrich_records([changed], 'v1')[0])
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), sha_before)
            self.assertEqual(json.loads(raw.read_text()), original)
            with self.assertRaises(FileExistsError):
                build(database, entries_path, blocks_path, root / 'report.json', ['测试草'])
            entries_path.write_text(json.dumps({'entries': []}))
            with self.assertRaises(ValueError):
                build(database, entries_path, blocks_path, root / 'changed.json', ['测试草'])
            with closing(sqlite3.connect(database)) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM identity_comparisons').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
