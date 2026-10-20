"""Relationship queries must resolve sources, retain provenance and count only explicit shared IDs."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_etcm_enrichment import build
from tcm.herbs import import_snapshot
from tcm.relation_queries import execute, parse


class RelationQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); source = self.root/'source'
        (source/'etcm_herbs').mkdir(parents=True)
        entries, blocks = [], []
        for i, (name, chinese) in enumerate([('Alpha', '测试甲草'), ('Beta', '测试乙草')]):
            targets = [{'Target Name': [f'{name} target {j}'], 'UniProt Accession Number': [f'P{j:05d}']} for j in range(23)]
            targets += [targets[0], {'Target Name': ['Only same name']},
                        {'Target Name': ['Different ID same name'], 'UniProt Accession Number': [f'Q{i:05d}']}]
            fields = [{'key': 'Herb Name in Pinyin', 'value': name}, {'key': 'Medicinal Part', 'value': 'Root'}]
            raw = {'code': 1, 'data': [{'id': 'base_information', 'value': fields},
                   {'id': 'related_table', 'value': [{'type': 'target', 'value': targets},
                    {'type': 'ingredient', 'value': [{'Ingredient Name': [f'Ingredient {j}']} for j in range(25)]}]}],
                   'complete_fetch_summary': [{'section': 'related_table', 'type': 'target', 'status': 'partial'}]}
            (source/'etcm_herbs'/f'{name}.json').write_text(json.dumps(raw))
            entries.append({'id': f'entry-{i}', 'name_zh_candidate': chinese, 'title_flags': [], 'title_block_ids': [f'b-{i}']})
            blocks.append({'id': f'b-{i}', 'raw': {'text': f'{name} SYNTHETIC HERB'}})
        self.base = self.root/'herbs.sqlite3'; import_snapshot(source, self.base, 'v1')
        ep, bp = self.root/'entries.json', self.root/'blocks.json'
        ep.write_text(json.dumps({'entries': entries})); bp.write_text(json.dumps(blocks))
        with redirect_stdout(io.StringIO()):build(source, self.base, 'v1', self.root/'derived', ep, bp)
        self.database = self.root/'derived/enrichment.sqlite3'
        env = patch.dict(os.environ, {'TCM_DB': str(self.base), 'TCM_DATASET_VERSION': 'v1',
                         'ETCM_ENRICHMENT_DB': str(self.database), 'ENABLE_NATURAL_ROUTING': '1'})
        env.start(); self.addCleanup(env.stop)

    def test_parser_is_bounded_and_does_not_capture_clinical_questions(self):
        for text in ('测试甲草有哪些成分', '测试甲草的化学成分有哪些', '查一下Alpha的成分'):
            self.assertEqual(parse(text)['kind'], 'ingredient')
        self.assertEqual(parse('Alpha与Beta有哪些共同靶点')['entities'], ['Alpha', 'Beta'])
        for text in ('Alpha的功效有哪些', 'Alpha有哪些成分能治疗什么疾病', '你好'):
            self.assertIsNone(parse(text))
        for text in ('Alpha和Beta和Gamma有哪些共同靶点', '/relations 成分 Alpha 第0页', '/relations 成分 Alpha 第1001页'):
            self.assertEqual(parse(text, explicit=text.startswith('/')).get('error'), 'invalid_query')

    def test_router_prefers_relationships_and_preserves_existing_routes(self):
        from tcm.assistant_router import resolve
        self.assertEqual(resolve('药材Alpha有哪些成分')['request']['topic'], 'etcm_relations')
        self.assertEqual(resolve('Alpha和Beta有哪些共同靶点')['reason'], 'etcm_relation_query')
        self.assertEqual(resolve('/relations 成分 Alpha')['mode'], 'explicit')
        self.assertEqual(resolve('收录多少药材')['request']['topic'], 'overview')
        self.assertEqual(resolve('Alpha的药性')['tool'], 'herb')
        with patch.dict(os.environ, {'ENABLE_NATURAL_ROUTING': '0'}):
            self.assertIsNone(resolve('Alpha有哪些成分'))
            self.assertEqual(resolve('/relations 成分 Alpha')['tool'], 'assistant')

    def test_chinese_candidates_require_selection_for_both_entities(self):
        result = execute(parse('测试甲草和测试乙草有哪些共同靶点'))
        self.assertEqual(result['status'], 'clarification_needed')
        self.assertNotIn('data', result['relations'])
        self.assertEqual([g['resolution_status'] for g in result['relations']['groups']], ['name_mapping_candidates']*2)
        ids = [g['options'][0]['source_record_id'] for g in result['relations']['groups']]
        chosen = execute(parse('/relations 共同靶点 '+' | '.join(ids), explicit=True))
        self.assertEqual(chosen['relations']['data']['total'], 23)
        self.assertEqual(chosen['llm_calls'], 0)

    def test_single_source_pagination_preserves_rows_and_capture_status(self):
        first = execute(parse('Alpha有哪些成分'))['relations']['data']
        second = execute(parse('/relations 成分 Alpha 第2页', explicit=True))['relations']['data']
        self.assertEqual((first['total'], len(first['items']), len(second['items'])), (25, 20, 5))
        self.assertFalse({x['edge_id'] for x in first['items']} & {x['edge_id'] for x in second['items']})
        self.assertTrue(first['items'][0]['source_pointer'].startswith('/data/1/value/1/value/'))
        targets = execute(parse('Alpha的靶点'))['relations']['data']
        self.assertEqual(targets['items'][0]['capture_status'][0]['status'], 'partial')

    def test_intersection_deduplicates_identifiers_not_names_and_keeps_both_witnesses(self):
        data = execute(parse('Alpha和Beta的共同靶点'))['relations']['data']
        self.assertEqual(data['total'], 23)
        first = data['items'][0]
        self.assertEqual(first['identifier'], 'P00000')
        self.assertEqual([s['source_row_count'] for s in first['sources']], [2, 2])
        for source in first['sources']:
            self.assertEqual(source['witnesses'][0]['capture_status'][0]['status'], 'partial')
            self.assertIn('raw_row', source['witnesses'][0])
        page2 = execute(parse('/relations 共同靶点 Alpha | Beta 第2页', explicit=True))['relations']['data']
        self.assertEqual(len(page2['items']), 3)
        self.assertFalse({x['identifier'] for x in data['items']} & {x['identifier'] for x in page2['items']})

    def test_missing_disabled_stale_and_same_source_do_not_fabricate_results(self):
        self.assertEqual(execute(parse('Unknown的成分'))['status'], 'clarification_needed')
        self.assertEqual(execute(parse('Alpha和Alpha的共同靶点'))['status'], 'clarification_needed')
        with patch.dict(os.environ, {'ETCM_ENRICHMENT_DB': ''}):
            self.assertEqual(execute(parse('Alpha的成分'))['status'], 'unavailable')
        with patch.dict(os.environ, {'TCM_DATASET_VERSION': 'other'}):
            self.assertEqual(execute(parse('Alpha的成分'))['status'], 'unavailable')
        import sqlite3
        with sqlite3.connect(self.database) as con:con.execute("UPDATE herbs SET file_sha256='changed'")
        self.assertEqual(execute(parse('Alpha和Beta的共同靶点'))['status'], 'unavailable')
        self.assertEqual(execute(parse('Alpha的成分'))['status'], 'unavailable')

    def test_empty_intersection_is_not_claimed_as_absence(self):
        import sqlite3
        with sqlite3.connect(self.database) as con:con.execute("UPDATE nodes SET identifier=NULL WHERE kind='target'")
        result = execute(parse('Alpha和Beta的共同靶点'))
        self.assertEqual(result['relations']['data']['total'], 0)
        self.assertIn('不表示不存在', result['message'])

    def test_graph_api_bypasses_models_and_clears_previous_focus(self):
        import app
        import agents.agent_decision as ad
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        def forbidden(*a, **kw):raise AssertionError('Relationship query called a model')
        before = hashlib.sha256(self.base.read_bytes()).hexdigest()
        with patch.object(ad, 'memory', ConversationStore(self.root/'memory.sqlite3')), \
             patch.object(ad.config.agent_decision, 'llm', RunnableLambda(forbidden)), \
             patch.object(ad.config.conversation, 'llm', RunnableLambda(forbidden)), \
             patch.object(ad.LocalGuardrails, 'check_input', forbidden), \
             patch.object(ad.LocalGuardrails, 'check_output', forbidden), TestClient(app.app) as client:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(client.post('/chat', json={'query': '/herb Alpha'}).status_code, 200)
                response = client.post('/chat', json={'query': 'Alpha和Beta有哪些共同靶点'})
                data = response.json()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(data['assistant']['relations']['data']['total'], 23)
                self.assertEqual(data['routing']['reason'], 'etcm_relation_query')
                conversation = client.get('/conversation').json()
                self.assertNotIn('tcm', conversation)
                self.assertEqual(conversation['assistant']['relations']['data']['total'],23)
                snapshot=ad.memory.load_serialized(client.cookies[app.SESSION_COOKIE_NAME])
                self.assertNotIn('raw_row',json.dumps(snapshot.tcm_context)+json.dumps(snapshot.messages))
                with patch.dict(os.environ, {'ETCM_ENRICHMENT_DB': ''}):
                    self.assertEqual(client.post('/chat', json={'query': 'Alpha有哪些成分'}).status_code, 503)
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
