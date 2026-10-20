"""Evidence exports preserve ownership, source checks, saved revisions and scope."""
import json
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_research_bookmarks as fixtures
from tcm.bookmark_report import export_note
from tcm.research_bookmarks import BookmarkStale


class ReportApiTests(unittest.TestCase):
    def setUp(self):fixtures.ApiTests.setUp(self)
    select = fixtures.ApiTests.select
    save = fixtures.ApiTests.save

    def test_research_export_contains_page_evidence_but_notes_are_opt_in(self):
        item = self.save(note='unique-personal-note').json()['item']
        path = '/api/research/bookmarks/' + item['id'] + '/report?revision=1'
        before = self.client.get('/conversation').json()
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['content-disposition'])
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertIn('药典单药证据清单', response.text)
        self.assertIn('物理页码', response.text)
        self.assertIn('来源 SHA256：' + 'a' * 64, response.text)
        self.assertNotIn('unique-personal-note', response.text)
        with_note = self.client.get(path + '&include_note=true')
        self.assertIn('个人笔记（用户记录，不作为资料证据）', with_note.text)
        self.assertIn('unique-personal-note', with_note.text)
        self.assertEqual(self.client.get('/conversation').json(), before)

    def test_owner_revision_and_source_failures_never_return_a_download(self):
        from fastapi.testclient import TestClient
        item = self.save().json()['item'];base = '/api/research/bookmarks/' + item['id']
        with TestClient(self.app.app) as other:
            other.get('/api/research/bookmarks')
            self.assertEqual(other.get(base + '/report?revision=1').status_code, 404)
        self.client.patch(base, json={'revision': 1, 'title': 'changed'})
        self.assertEqual(self.client.get(base + '/report?revision=1').status_code, 409)
        with patch('tcm.mineru_chat.ResearchTool.valid_context', return_value=False):
            response = self.client.get(base + '/report?revision=2')
            self.assertEqual(response.status_code, 409)
            self.assertNotIn('content-disposition', response.headers)
        with patch.object(self.ad.research_runtime, 'get_service', return_value=None):
            self.assertEqual(self.client.get(base + '/report?revision=2').status_code, 503)

    def test_edit_during_export_is_detected_without_overwriting_notes(self):
        import tcm.bookmark_report as reports
        item = self.save().json()['item'];base = '/api/research/bookmarks/' + item['id']
        original = reports.export_note
        def concurrent(*args, **kwargs):
            result = original(*args, **kwargs)
            self.client.patch(base, json={'revision': 1, 'title': 'new title', 'note': 'new note'})
            return result
        with patch.object(reports, 'export_note', side_effect=concurrent):
            response = self.client.get(base + '/report?revision=1&include_note=true')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get('/api/research/bookmarks').json()['items'][0]['note'], 'new note')


class RelationReportTests(unittest.TestCase):
    def setUp(self):fixtures.RelationBookmarkTests.setUp(self)

    def item(self, question):
        from tcm.relation_memory import execute_turn
        from tcm.relation_queries import parse
        from tcm.agent_research import pack_contexts
        from tcm.research_bookmarks import capture
        _, focus = execute_turn(parse(question), None)
        return {'title': '# hostile title <script>x</script>', 'note': '```\n# note-not-evidence\n```',
                'revision': 1, 'payload': capture(pack_contexts(None, None, None, focus))}

    def test_path_report_is_only_saved_second_page_with_both_source_witnesses(self):
        item = self.item('TestHerb的成分关联哪些靶点 第2页')
        text = export_note(item, None)
        self.assertIn('收藏页码：2', text)
        self.assertIn('本页返回：6项', text)
        self.assertEqual(text.count('### 本页第'), 6)
        self.assertIn('herb_evidence', text);self.assertIn('target_evidence', text)
        self.assertIn('json_pointer', text);self.assertIn('file_sha256', text)
        self.assertIn('目录覆盖（不是检索召回率）', text)
        self.assertNotIn('note-not-evidence', text)
        self.assertNotIn('<script>', text)
        included = export_note(item, None, include_note=True)
        self.assertIn('````text\n```\n# note-not-evidence\n```\n````', included)

    def test_ingredient_and_target_reports_preserve_raw_rows(self):
        for query in ('TestHerb有哪些成分', '/relations 靶点成分 P12345'):
            from tcm.relation_memory import execute_turn
            from tcm.relation_queries import parse
            from tcm.agent_research import pack_contexts
            from tcm.research_bookmarks import capture
            _, focus = execute_turn(parse(query, explicit=query.startswith('/')), None)
            item = {'title': 'test', 'note': '', 'revision': 1,
                    'payload': capture(pack_contexts(None, None, None, focus))}
            text = export_note(item, None)
            self.assertIn('raw_row', text)
            self.assertIn('capture_status', text)
            self.assertIn('当前目录总数', text)

    def test_source_change_rejects_report(self):
        item = self.item('TestHerb有哪些成分')
        with sqlite3.connect(self.enrichment) as con:
            con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='source_fingerprint'")
        with self.assertRaises(BookmarkStale):export_note(item, None)


if __name__ == '__main__':unittest.main()
