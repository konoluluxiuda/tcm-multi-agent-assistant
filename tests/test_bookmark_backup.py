"""Portable backups must not transfer ownership, overwrite notes or trust source claims."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tcm.bookmark_backup import (MAX_BYTES, InvalidBookmarkBackup, canonical, decode, encode)
from tcm.research_bookmarks import BookmarkStore, BookmarkLimit
from tests import test_research_bookmarks as fixtures


def item(question='示例叶炭的功效', note='个人笔记'):
    return {'title': '研究收藏', 'note': note, 'payload': {'kind': 'research', 'focus': {
        'kind': 'mineru_research_chat_v1', 'name': '示例叶炭', 'last_question': question,
        **{k: 'a' * 64 for k in ('index_sha256', 'view_sha256', 'retriever_sha256', 'guards_sha256')}
    }}}


def reseal(data):
    body = {k: v for k, v in data.items() if k != 'sha256'}
    return canonical({**body, 'sha256': hashlib.sha256(canonical(body)).hexdigest()})


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = BookmarkStore(Path(self.temp.name) / 'saved.sqlite3')
        self.cookie = self.store.new_cookie()

    def test_all_pages_export_restore_uses_new_ids_and_new_owner(self):
        ids = []
        for i in range(21):
            row = item('示例叶炭的功效' + str(i))
            saved, _ = self.store.create(self.cookie, row['title'], row['note'], row['payload'])
            ids.append(saved['id'])
        raw = self.store.export_backup(self.cookie)
        self.assertEqual(len(decode(raw)), 21)
        self.assertNotIn(self.cookie.encode(), raw)
        self.assertNotIn(b'owner', raw)
        self.assertTrue(all(identifier.encode() not in raw for identifier in ids))
        other = self.store.new_cookie()
        result = self.store.import_backup(other, raw)
        self.assertEqual(result['imported'], 21)
        self.assertFalse(result['sources_checked'])
        self.assertTrue(set(ids).isdisjoint(r['id'] for r in self.store.list(other)['items']))
        self.assertEqual(self.store.list(self.cookie)['total'], 21)

    def test_reimport_preserves_existing_note_and_deduplicates_file(self):
        row = item();saved, _ = self.store.create(self.cookie, row['title'], '本地新笔记', row['payload'])
        self.assertEqual(self.store.import_backup(self.cookie, encode([row, row]))['skipped_duplicates'], 2)
        self.assertEqual(self.store.get(self.cookie, saved['id'])['note'], '本地新笔记')
        other = self.store.new_cookie()
        result = self.store.import_backup(other, encode([row, row]))
        self.assertEqual((result['imported'], result['skipped_duplicates']), (1, 1))

    def test_bad_checksum_unknown_fields_and_future_versions_are_rejected(self):
        data = json.loads(encode([item()]))
        data['items'][0]['note'] = '损坏修改'
        with self.assertRaises(InvalidBookmarkBackup):decode(canonical(data))
        for change in ({'version': 2}, {'version': True}, {'owner': 'forged-owner'}):
            changed = {**json.loads(encode([item()])), **change}
            with self.assertRaises(InvalidBookmarkBackup):decode(reseal(changed))
        with self.assertRaises(InvalidBookmarkBackup):decode(b'{"format":"first","format":"second"}')
        with self.assertRaises(InvalidBookmarkBackup):decode(b'{"version":NaN}')

    def test_invalid_later_item_does_not_partially_import(self):
        data = json.loads(encode([item(), item('示例叶炭的用量')]))
        data['items'][1]['payload']['focus']['unexpected'] = 'private path or arbitrary instruction'
        with self.assertRaises(InvalidBookmarkBackup):self.store.import_backup(self.cookie, reseal(data))
        self.assertEqual(self.store.list(self.cookie)['total'], 0)
        data = json.loads(encode([item()]))
        data['items'][0]['note'] = 'x' * 2001
        with self.assertRaises(InvalidBookmarkBackup):self.store.import_backup(self.cookie, reseal(data))

    def test_capacity_failure_does_not_insert_first_new_item(self):
        original = item();self.store.create(self.cookie, original['title'], '', original['payload'])
        raw = encode([item('示例叶炭的用量'), item('示例叶炭的贮藏')])
        with patch.object(self.store, 'LIMIT', 2), self.assertRaises(BookmarkLimit):
            self.store.import_backup(self.cookie, raw)
        self.assertEqual(self.store.list(self.cookie)['total'], 1)

    def test_invalid_relation_shape_and_oversized_input_are_rejected(self):
        relation = {'title': '路径', 'note': '', 'payload': {'kind': 'relation', 'focus': {
            'kind': 'etcm_relation_focus_v1', 'query_kind': 'herb_target_paths', 'page': 2,
            'dataset_version': 'v1', 'herb_source_fingerprint': 'a' * 64,
            'target_export_sha256': 'b' * 64,
            'herb_sources': [{'source_record_id': 'etcm2-local:' + 'c' * 64, 'file_sha256': 'd' * 64}]
        }}}
        self.assertEqual(decode(encode([relation]))[0]['payload']['focus']['page'], 2)
        relation['payload']['focus']['page'] = True
        with self.assertRaises(InvalidBookmarkBackup):encode([relation])
        with self.assertRaises(InvalidBookmarkBackup):decode(b' ' * (MAX_BYTES + 1))


class BackupApiTests(unittest.TestCase):
    def setUp(self):fixtures.ApiTests.setUp(self)
    select = fixtures.ApiTests.select
    save = fixtures.ApiTests.save

    def test_download_and_other_browser_restore_do_not_transfer_original_access(self):
        from fastapi.testclient import TestClient
        original = self.save(note='可携带的研究笔记').json()['item']
        response = self.client.get('/api/research/bookmarks/export')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['content-disposition'])
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertNotIn(b'cookie', response.content)
        with TestClient(self.app.app) as other:
            other.get('/api/research/bookmarks')
            restored = other.post('/api/research/bookmarks/import', content=response.content,
                                  headers={'Content-Type': 'application/json'})
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(restored.json()['imported'], 1)
            row = other.get('/api/research/bookmarks').json()['items'][0]
            self.assertEqual(row['note'], '可携带的研究笔记')
            self.assertNotEqual(row['id'], original['id'])
            self.assertEqual(other.post('/api/research/bookmarks/' + row['id'] + '/preview').status_code, 200)
            self.assertEqual(other.post('/api/research/bookmarks/' + original['id'] + '/preview').status_code, 404)

    def test_import_retains_old_bindings_without_querying_sources(self):
        raw = encode([item()])
        with patch.object(self.ad.research_runtime, 'get_service', side_effect=AssertionError('import queried sources')):
            response = self.client.post('/api/research/bookmarks/import', content=raw,
                                        headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200)
        identifier = self.client.get('/api/research/bookmarks').json()['items'][0]['id']
        self.assertEqual(self.client.post('/api/research/bookmarks/' + identifier + '/preview').status_code, 409)

    def test_forged_query_name_is_not_accepted_as_the_bound_source(self):
        self.save();data = self.client.get('/api/research/bookmarks/export').json()
        data['items'][0]['payload']['focus']['name'] = '另一个名字'
        response = self.client.post('/api/research/bookmarks/import', content=reseal(data),
                                    headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 200)  # Shape and source approval are separate.
        rows = self.client.get('/api/research/bookmarks').json()['items']
        with patch('tcm.mineru_chat.ResearchTool.valid_context', return_value=True):
            results = [self.client.post('/api/research/bookmarks/' + r['id'] + '/preview').status_code for r in rows]
        self.assertEqual(sorted(results), [200, 409])

    def test_invalid_large_wrong_media_and_unauthenticated_import(self):
        response = self.client.post('/api/research/bookmarks/import', content=b'not JSON',
                                    headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post('/api/research/bookmarks/import', content=b'x').status_code, 415)
        response = self.client.post('/api/research/bookmarks/import', content=b' ' * (MAX_BYTES + 1),
                                    headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get('/api/research/bookmarks').json()['total'], 0)
        from fastapi.testclient import TestClient
        with TestClient(self.app.app) as other:
            self.assertEqual(other.get('/api/research/bookmarks/export').status_code, 401)
            self.assertEqual(other.post('/api/research/bookmarks/import', content=encode([]),
                                       headers={'Content-Type': 'application/json'}).status_code, 401)


if __name__ == '__main__':unittest.main()
