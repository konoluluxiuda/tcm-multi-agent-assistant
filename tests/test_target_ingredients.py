"""Audited reverse lookup must stay source-bound, paginated and model-free."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_target_ingredient_index import build
from tcm.target_ingredients import connect, execute, lookup


class TargetIngredientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.audit = self.root/'audit'; self.audit.mkdir()
        self.rows, contents = [], []
        for i, (identifier, gene, count) in enumerate([('P12345', 'EX1', 25), ('Q12345', 'EX2', 1)]):
            identity = {'Target Name': 'Synthetic target '+str(i), 'Gene Symbol': gene, 'UniProt Accession Number': identifier}
            sha = str(i)*64; path = f'etcm_targets/synthetic_{i}.json'
            contents.append({'status': 'source_identity_consistent_not_externally_verified', 'identity': identity,
                             'file_sha256': sha, 'canonical_source': path})
            for j in range(count):
                self.rows.append({'target': identity, 'source_file': path, 'source_file_sha256': sha,
                    'ingredient_name': 'Synthetic compound '+str(j),
                    'raw_row': {'Ingredient Name': ['Synthetic compound '+str(j)], 'Molecular Formula': 'C2H6'},
                    'json_pointer': f'/data/1/value/0/value/{j}', 'capture_status': [{'status': 'partial'}],
                    'ingredient_identity_verified': False})
        self.report = {'status': 'completed', 'contents': contents, 'dataset_version': 'v1', 'herb_source_fingerprint': 'herb-sha',
                       'exported_target_ingredient_rows': len(self.rows), 'current_herb_uniprot_groups': 4}
        self.write_audit()
        self.index = self.root/'targets.sqlite3'; build(self.audit, self.index)
        self.enrichment = self.root/'enrichment.sqlite3'
        with sqlite3.connect(self.enrichment) as con:
            con.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)')
            meta = {'kind': 'etcm_enrichment_v1', 'build_complete': True, 'dataset_version': 'v1', 'source_fingerprint': 'herb-sha'}
            con.executemany('INSERT INTO metadata VALUES (?,?)', [(k, json.dumps(v)) for k,v in meta.items()])
        env = patch.dict(os.environ, {'ETCM_TARGET_DB': str(self.index), 'ETCM_ENRICHMENT_DB': str(self.enrichment),
                                     'ENABLE_NATURAL_ROUTING': '1', 'RESEARCH_TOOLS_ONLY': '1'})
        env.start(); self.addCleanup(env.stop)

    def write_audit(self):
        (self.audit/'report.json').write_text(json.dumps(self.report))
        (self.audit/'target_ingredients.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in self.rows))

    def test_gene_and_uniprot_are_exact_and_pages_preserve_source_rows(self):
        first = lookup(self.index, 'ex1'); second = lookup(self.index, 'P12345', page=2)
        self.assertEqual(first['target']['uniprot'], 'P12345')
        self.assertEqual(first['data']['total'], 25)
        self.assertEqual(len(second['data']['items']), 5)
        self.assertFalse({r['json_pointer'] for r in first['data']['items']} & {r['json_pointer'] for r in second['data']['items']})
        self.assertEqual(first['data']['items'][0]['capture_status'][0]['status'], 'partial')
        self.assertFalse(first['data']['items'][0]['ingredient_identity_verified'])
        self.assertEqual(lookup(self.index, 'EX')['resolution_status'], 'not_covered')
        for token, page in [('EX1',0), ('EX1',1001), ('x\' OR 1=1',1)]:
            with self.assertRaises(ValueError):lookup(self.index, token, page=page)

    def test_gene_ambiguity_requires_uniprot_selection(self):
        with sqlite3.connect(self.index) as con:con.execute("UPDATE targets SET gene='EX1',gene_key='EX1' WHERE uniprot='Q12345'")
        result = execute({'entities':['EX1'],'page':1})
        self.assertEqual(result['status'], 'clarification_needed')
        self.assertEqual(result['relations']['resolution_status'], 'ambiguous_target')
        self.assertEqual(len(result['relations']['options']), 2)
        self.assertNotIn('data', result['relations'])
        self.assertEqual(lookup(self.index, 'Q12345')['data']['total'], 1)

    def test_disabled_missing_not_covered_and_stale_are_explicit(self):
        request = {'entities':['EX1'],'page':1}
        with patch.dict(os.environ, {'ETCM_TARGET_DB':''}):self.assertEqual(execute(request)['status'], 'unavailable')
        absent = self.root/'absent.sqlite3'
        with patch.dict(os.environ, {'ETCM_TARGET_DB':str(absent)}):self.assertEqual(execute(request)['status'], 'unavailable')
        self.assertFalse(absent.exists())
        result = execute({'entities':['P99999'],'page':1})
        self.assertEqual(result['relations']['resolution_status'], 'not_covered')
        self.assertIn('不表示', result['message'])
        with sqlite3.connect(self.enrichment) as con:con.execute("UPDATE metadata SET value='\"other-sha\"' WHERE key='source_fingerprint'")
        self.assertEqual(execute(request)['status'], 'unavailable')

    def test_builder_rejects_unapproved_rows_duplicates_and_incomplete_outputs(self):
        self.rows[0]['target'] = {**self.rows[0]['target'], 'Gene Symbol':'WRONG'}
        self.write_audit()
        bad = self.root/'bad.sqlite3'
        with self.assertRaises(ValueError):build(self.audit, bad)
        with self.assertRaises(ValueError):connect(bad)
        self.rows[0]['target'] = self.report['contents'][0]['identity']
        self.rows.append(self.rows[0]);self.report['exported_target_ingredient_rows'] += 1;self.write_audit()
        with self.assertRaises(sqlite3.IntegrityError):build(self.audit, self.root/'duplicate.sqlite3')
        with self.assertRaises(FileExistsError):build(self.audit, self.index)

    def test_parser_preserves_herbs_and_supports_explicit_target_direction(self):
        from tcm.relation_queries import parse
        for text in ('CYP24A1有哪些成分','靶点TNF的成分有哪些','Q07973的关联成分'):
            self.assertEqual(parse(text)['kind'], 'target_ingredient')
        self.assertEqual(parse('BaiZhu有哪些成分')['kind'], 'ingredient')
        self.assertEqual(parse('/relations 靶点成分 EX1 第2页',explicit=True)['page'], 2)
        self.assertEqual(parse('靶点芳香化酶有哪些成分').get('error'), 'invalid_query')
        self.assertIsNone(parse('CYP24A1有哪些成分可以治疗糖尿病'))

    def test_graph_api_bypasses_models_and_memory_keeps_only_fixed_summary(self):
        import app
        import agents.agent_decision as ad
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        def forbidden(*args, **kwargs):raise AssertionError('Reverse query called a model')
        with patch.object(ad,'memory',ConversationStore(self.root/'memory.sqlite3')), \
             patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.LocalGuardrails,'check_input',forbidden), patch.object(ad.LocalGuardrails,'check_output',forbidden), \
             TestClient(app.app) as client, redirect_stdout(io.StringIO()):
            response = client.post('/chat',json={'query':'EX1有哪些成分'})
            self.assertEqual(response.status_code,200)
            payload = response.json()['assistant']
            self.assertEqual(payload['relations']['data']['total'],25); self.assertEqual(payload['llm_calls'],0)
            self.assertEqual(client.get('/conversation').json()['assistant']['relations']['data']['total'],25)
            snapshot=ad.memory.load_serialized(client.cookies[app.SESSION_COOKIE_NAME])
            self.assertNotIn('Synthetic compound',json.dumps(snapshot.tcm_context)+json.dumps(snapshot.messages))
            with patch.dict(os.environ,{'ETCM_TARGET_DB':''}):
                self.assertEqual(client.post('/chat',json={'query':'EX1有哪些成分'}).status_code,503)


if __name__ == '__main__':
    unittest.main()
