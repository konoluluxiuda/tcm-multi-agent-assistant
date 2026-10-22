"""Disease enrichment rows are source associations, never indications or recommendations."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_etcm_enrichment import build
from tcm.herbs import import_snapshot
from tcm.relation_queries import parse, execute
from tcm.relation_memory import execute_turn


class DiseaseRelationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name);self.source = self.root / 'source'
        (self.source / 'etcm_herbs').mkdir(parents=True)
        entries, blocks = [], []
        for name, chinese, state in [('Alpha', '测试甲草', 'partial'), ('Beta', '测试乙草', 'skipped'), ('Gamma', '测试丙草', None)]:
            rows = [{'Disease Name': ['Repeated' if j < 2 else f'Disease {j}'],
                     'Global category': 'source category', 'Anatomical category': 'source anatomy'} for j in range(25)] if name == 'Alpha' else []
            raw = {'code': 1, 'data': [{'id': 'base_information', 'value': [
                {'key': 'Herb Name in Pinyin', 'value': name}, {'key': 'Medicinal Part', 'value': 'Root'}]},
                {'id': 'related_table', 'value': [{'type': 'disease', 'value': rows}]}]}
            if state:raw['complete_fetch_summary'] = [{'section': 'related_table', 'type': 'disease', 'status': state}]
            (self.source / 'etcm_herbs' / (name + '.json')).write_text(json.dumps(raw))
            entries.append({'id': name, 'name_zh_candidate': chinese, 'title_flags': [], 'title_block_ids': [name]})
            blocks.append({'id': name, 'raw': {'text': name + ' SYNTHETIC HERB'}})
        self.base = self.root / 'herbs.sqlite3';import_snapshot(self.source, self.base, 'v1')
        ep, bp = self.root / 'entries.json', self.root / 'blocks.json'
        ep.write_text(json.dumps({'entries': entries}));bp.write_text(json.dumps(blocks))
        with redirect_stdout(io.StringIO()):build(self.source, self.base, 'v1', self.root / 'derived', ep, bp)
        self.database = self.root / 'derived/enrichment.sqlite3'
        env = patch.dict(os.environ, {'TCM_DB': str(self.base), 'TCM_DATASET_VERSION': 'v1',
            'ETCM_ENRICHMENT_DB': str(self.database), 'ENABLE_NATURAL_ROUTING': '1',
            'RESEARCH_TOOLS_ONLY': '1', 'ENABLE_RESEARCH_BOOKMARKS': '1',
            'RESEARCH_BOOKMARK_DB': str(self.root / 'bookmarks.sqlite3')})
        env.start();self.addCleanup(env.stop)

    def test_bounded_intent_and_clinical_queries_remain_separate(self):
        from tcm.assistant_router import resolve
        for question in ('Alpha有哪些疾病富集记录', 'Alpha的富集疾病', '查询Alpha的来源疾病富集记录'):
            self.assertEqual(parse(question)['kind'], 'disease')
            self.assertEqual(resolve(question)['reason'], 'etcm_relation_query')
        for question in ('Alpha能治疗哪些疾病', 'Alpha治疗糖尿病吗', 'Alpha有哪些疾病富集记录并推荐用药'):
            self.assertIsNone(parse(question))
        self.assertEqual(parse('Alpha和Beta的疾病富集')['error'], 'invalid_query')
        self.assertEqual(parse('/relations 疾病富集 Alpha 第1001页', explicit=True)['error'], 'invalid_query')

    def test_selected_source_paging_retains_duplicate_rows_and_raw_provenance(self):
        before = hashlib.sha256(self.base.read_bytes()).hexdigest()
        choice = execute(parse('测试甲草有哪些疾病富集记录'))
        self.assertEqual(choice['status'], 'clarification_needed')
        identifier = choice['relations']['groups'][0]['options'][0]['source_record_id']
        first = execute(parse('/relations 疾病富集 ' + identifier, explicit=True))
        second = execute(parse('/relations 疾病富集 ' + identifier + ' 第2页', explicit=True))
        a, b = first['relations']['data'], second['relations']['data']
        self.assertEqual((a['total'], len(a['items']), len(b['items'])), (25, 20, 5))
        self.assertFalse({r['edge_id'] for r in a['items']} & {r['edge_id'] for r in b['items']})
        self.assertEqual(sum(r['name'] == 'Repeated' for r in a['items']), 2)
        self.assertEqual(a['items'][0]['capture_status'][0]['status'], 'partial')
        self.assertEqual(a['items'][0]['raw_row']['Global category'], 'source category')
        self.assertTrue(a['items'][0]['source_pointer'].startswith('/data/1/value/0/value/'))
        self.assertIn('不是适应证',''.join(first['notes']))
        self.assertEqual(hashlib.sha256(self.base.read_bytes()).hexdigest(), before)

    def test_zero_rows_keep_skipped_and_unknown_capture_distinct(self):
        skipped = execute(parse('Beta的疾病富集'))
        unknown = execute(parse('Gamma的疾病富集'))
        self.assertEqual(skipped['relations']['data']['total'], 0)
        self.assertEqual(skipped['relations']['source_capture_status'][0]['status'], 'skipped')
        self.assertEqual(unknown['relations']['source_capture_status'], [])
        self.assertIn('不等于不存在', skipped['message'])

    def test_disabled_stale_and_changed_sources_fail_without_fallback(self):
        _, focus = execute_turn(parse('Alpha的疾病富集'), None)
        with patch.dict(os.environ, {'ETCM_ENRICHMENT_DB': ''}):
            self.assertEqual(execute(parse('Alpha的疾病富集'))['status'], 'unavailable')
        with sqlite3.connect(self.database) as con:
            con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='source_fingerprint'")
        result, new = execute_turn({'followup': True, 'action': 'replay'}, focus)
        self.assertEqual(result['status'], 'clarification_needed');self.assertIsNone(new)

    def test_new_relation_kind_roundtrips_backup_and_evidence_note(self):
        from tcm.agent_research import pack_contexts
        from tcm.research_bookmarks import capture, preview
        from tcm.bookmark_backup import encode, decode
        from tcm.bookmark_report import export_note
        _, focus = execute_turn(parse('/relations 疾病富集 Alpha 第2页', explicit=True), None)
        item = {'title': '疾病富集来源', 'note': '个人记录', 'payload': capture(pack_contexts(None, None, None, focus))}
        restored = decode(encode([item]))[0]
        result = preview(restored['payload'], None)['assistant']
        self.assertEqual(result['relations']['page'], 2)
        text = export_note({**restored, 'revision': 1}, None)
        self.assertIn('疾病富集记录不是适应证', text)
        self.assertIn('本页返回：5项', text)
        self.assertIn('partial', text)
        self.assertNotIn('个人记录', text)

    def test_main_chat_refresh_bookmark_and_download_use_no_model(self):
        import app
        import agents.agent_decision as ad
        from agents.conversation_store import ConversationStore
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        def forbidden(*a, **kw):raise AssertionError('Disease relation reached a model')
        with patch.object(ad, 'memory', ConversationStore(self.root / 'chat.sqlite3')), \
             patch.object(ad.config.agent_decision, 'llm', RunnableLambda(forbidden)), \
             patch.object(ad.config.conversation, 'llm', RunnableLambda(forbidden)), \
             patch.object(ad.LocalGuardrails, 'check_input', forbidden), \
             patch.object(ad.LocalGuardrails, 'check_output', forbidden), TestClient(app.app) as client, redirect_stdout(io.StringIO()):
            client.get('/api/research/bookmarks')
            data = client.post('/chat', json={'query': 'Alpha有哪些疾病富集记录'}).json()
            self.assertEqual(data['assistant']['relations']['data']['total'], 25)
            response = client.post('/chat', json={'query': '/relations 疾病富集 Alpha 第2页'})
            self.assertEqual(response.status_code, 200)
            restored = client.get('/conversation').json()['assistant']
            self.assertEqual(restored['relations']['page'], 2)
            revision = client.get('/api/research/bookmarks').json()['conversation_revision']
            saved = client.post('/api/research/bookmarks', json={'expected_revision': revision}).json()['item']
            report = client.get('/api/research/bookmarks/' + saved['id'] + '/report?revision=1')
            self.assertEqual(report.status_code, 200)
            self.assertIn('本页返回：5项', report.text)
            self.assertEqual(client.get('/api/research/bookmarks/export').status_code, 200)


if __name__ == '__main__':unittest.main()
