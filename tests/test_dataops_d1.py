import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests.dataops_fixture import make_sources
from tcm.dataops.contracts import Goal, Query
from tcm.dataops.baseline import collect, execute, validate_result
from tcm.dataops.workspace import Workspaces, Conflict
from tcm.dataops.artifacts import publish, verify
from tcm.dataops.data_access import SourceChanged


class DataOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.records, self.sources = make_sources(self.root)
        self.store = Workspaces(self.root / 'state')

    def test_query_selection_count_grouping_and_read_only(self):
        before = (self.root / 'herbs.sqlite3').read_bytes()
        with self.sources() as s:
            result = s.query(Query(filters=[{'field': 'medicinal_part', 'value': 'Leaf'}], limit=2, group_by='medicinal_part'))
            self.assertEqual(result['total'], 3); self.assertEqual(result['returned'], 2)
            self.assertEqual(result['groups'], [{'value': 'Leaf', 'count': 3}])
            self.assertEqual(result['next_offset'], 2)
            nxt = s.query(Query(filters=[{'field': 'medicinal_part', 'value': 'Leaf'}], offset=2))
            self.assertFalse(set(result['selected_ids']) & set(nxt['selected_ids']))
            self.assertEqual(s.query(Query(filters=[{'field': 'family', 'op': 'missing'}]))['total'], 4)
            self.assertEqual(s.query(Query(filters=[{'field': 'properties', 'value': '%'}]))['total'], 0)
        self.assertEqual((self.root / 'herbs.sqlite3').read_bytes(), before)

    def test_contract_rejects_arbitrary_sql_unknown_fields_over_budget(self):
        for raw in ({'limit': 51}, {'group_by': "name);DROP TABLE records"}, {'source_ids': ['fake']},
                    {'filters': [{'field': 'raw_sql', 'value': 'x'}]}, {'filters': [{'field': 'family', 'value': ''}]}):
            with self.subTest(raw=raw), self.assertRaises(ValueError): Query.model_validate(raw)
        with self.assertRaises(ValueError): Goal(document_fields=['虚构字段'])

    def test_literal_names_support_chinese_candidates_and_preserve_unknown(self):
        with self.sources() as s:
            r = collect(s, Goal(query=Query(names=['Alpha', '乙草', '不存在的名称'])))
            self.assertEqual({row['name'] for row in r['rows']}, {'Alpha', 'Beta'})
            self.assertEqual(r['coverage']['unknown_names'], ['不存在的名称'])
            self.assertEqual(r['status'], 'partial')
            self.assertEqual(s.query(Query(names=['不存在的名称']))['total'], 0)

    def test_ambiguity_missing_and_part_difference_remain_visible(self):
        with self.sources() as s: result = collect(s, Goal(attributes=['properties'], document_fields=['功能与主治', '注意']))
        rows = {r['name']: r for r in result['rows']}
        self.assertEqual(rows['Alpha']['mapping_status'], 'fields_consistent_not_verified')
        self.assertEqual(rows['Beta']['mapping_status'], 'medicinal_part_difference')
        self.assertEqual(rows['Gamma']['mapping_status'], 'ambiguous')
        self.assertIsNone(rows['Gamma']['cells']['功能与主治']['value'])
        self.assertEqual(rows['Delta']['mapping_status'], 'unmatched')
        self.assertEqual(result['coverage']['missing_cells'], 6)
        self.assertEqual(result['status'], 'complete'); self.assertEqual(result['model_calls'], 0)
        self.assertTrue(all(not r['identity_verified'] for r in rows.values()))
        with self.sources() as s: strict = collect(s, Goal(completion_policy='require_all_fields'))
        self.assertEqual(strict['status'], 'partial')

    def test_omitted_selection_is_not_silent(self):
        with self.sources() as s:
            r = collect(s, Goal(query=Query(source_ids=['etcm2-local:' + 'f'*64])))
        self.assertEqual(r['status'], 'partial')
        self.assertEqual(r['issues'][0]['kind'], 'not_found')

    def test_prepared_slice_scope_is_visible_and_duplicates_are_not_merged(self):
        with self.sources() as s:
            r = collect(s, Goal(query=Query(names=['Alpha']), document_fields=['功能与主治', '贮藏']))
        row = r['rows'][0]
        self.assertEqual(row['cells']['功能与主治']['value'], '测试原文0')
        self.assertEqual(row['cells']['功能与主治']['status'], 'candidate_scope_value')
        self.assertIsNone(row['cells']['贮藏']['value'])
        self.assertEqual(row['cells']['贮藏']['status'], 'ambiguous_field')
        self.assertEqual(len(row['cells']['贮藏']['evidence_ids']), 2)
        row['cells']['功能与主治']['status'] = 'candidate_value'
        with self.assertRaises(ValueError): validate_result(r)

    def test_source_change_and_foreign_evidence_are_rejected(self):
        with self.sources() as s:
            r = collect(s, Goal())
            with sqlite3.connect(self.root / 'herbs.sqlite3') as con:
                # A read transaction may block a source writer: modify only the manifest instead.
                pass
            (self.root / 'index/manifest.json').write_text('{}')
            with self.assertRaises(SourceChanged): s.assert_unchanged()
        first = r['rows'][0]
        key = next(iter(r['evidence']))
        r['evidence'][key]['source_id'] = 'wrong'
        with self.assertRaises(ValueError): validate_result(r)

    def test_valid_reference_cannot_support_invented_or_wrong_field_value(self):
        with self.sources() as s: result = collect(s, Goal())
        from copy import deepcopy
        changed = deepcopy(result)
        changed['rows'][0]['cells']['medicinal_part']['value'] = 'Invented'
        with self.assertRaises(ValueError): validate_result(changed)
        changed = deepcopy(result)
        row = next(r for r in changed['rows'] if r['cells']['功能与主治']['value'])
        row['cells']['功能与主治']['value'] += '额外推断'
        with self.assertRaises(ValueError): validate_result(changed)

    def test_workbook_exact_reopen_no_formulas_and_tamper_detection(self):
        with self.sources() as s:
            doc, created = execute(self.store, 'owner', 'request-1', s, Goal(attributes=['properties']))
            replay, created2 = execute(self.store, 'owner', 'request-1', s, Goal(attributes=['properties']))
        self.assertTrue(created); self.assertFalse(created2); self.assertEqual(doc['id'], replay['id'])
        path, _ = self.store.artifact('owner', doc['id'])
        self.assertEqual(verify(path, doc['result'])['status'], 'passed')
        from openpyxl import load_workbook
        book = load_workbook(path); sheet = book['结果']
        formulas = [cell for row in sheet for cell in row if cell.data_type == 'f']
        self.assertFalse(formulas)
        self.assertIn('=1+2', [cell.value for row in sheet for cell in row])
        sheet.cell(2, 2).value = 'wrong'; book.save(path); book.close()
        with self.assertRaises(Conflict): self.store.artifact('owner', doc['id'])
        with self.assertRaises(ValueError): verify(path, doc['result'])

    def test_owner_idempotence_immutable_and_restart(self):
        with self.sources() as s:
            doc, _ = execute(self.store, 'a', 'request-1', s, Goal())
            with self.assertRaises(Conflict): execute(self.store, 'a', 'request-1', s, Goal(title='changed'))
        reopened = Workspaces(self.root / 'state')
        self.assertEqual(reopened.get('a', doc['id'])['artifact'], doc['artifact'])
        self.assertEqual(reopened.listing('b'), [])
        with self.assertRaises(KeyError): reopened.get('b', doc['id'])
        with self.assertRaises(Conflict): reopened.finish('a', doc['id'], error='overwrite')

    def test_publish_failure_never_exposes_partial_artifact(self):
        with self.sources() as s, patch('tcm.dataops.baseline.publish', side_effect=OSError('full')):
            with self.assertRaises(OSError): execute(self.store, 'a', 'request-1', s, Goal())
        entry = self.store.listing('a')[0]
        self.assertEqual(entry['status'], 'failed')
        with self.assertRaises(KeyError): self.store.artifact('a', entry['id'])


class RetiredLiteratureTests(unittest.TestCase):
    def test_flags_cannot_reactivate_network_search(self):
        from tcm.agent_pubmed import PubMedRuntime
        from tcm.pubmed import PubMedClient
        with patch.dict('os.environ', {'ENABLE_PUBMED': '1'}), patch('tcm.pubmed.urlopen', side_effect=AssertionError('network')):
            result, _ = PubMedRuntime().execute({'action': 'search', 'query': 'example'}, None)
            self.assertEqual(result['status'], 'pubmed_retired')
            self.assertEqual(PubMedClient().search('example')['error_code'], 'pubmed_retired')


if __name__ == '__main__': unittest.main()
