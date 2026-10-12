"""Filesystem boundaries, content verification and anonymous-session isolation."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agents.source_links import source_id, source_url
from source_access import PrivateArtifacts, SourceCatalog

PNG = b'\x89PNG\r\n\x1a\n' + b'private-image'


class SourceAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root/'data/raw/paper.pdf'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'%PDF-1.4\nregistered paper')
        self.identifier = source_id('data/raw/paper.pdf')
        manifest = self.root/'manifest.json'
        manifest.write_text(json.dumps({'version':1,'sources':{self.identifier:{'path':'data/raw/paper.pdf','sha256':hashlib.sha256(self.path.read_bytes()).hexdigest()}}}))
        self.catalog = SourceCatalog(self.root,manifest)

    def test_registered_content_and_legacy_id(self):
        self.assertEqual(self.catalog.read(self.identifier).content,self.path.read_bytes())
        self.assertEqual(self.catalog.legacy_id('http://localhost:8000/data/raw/paper.pdf'),self.identifier)
        self.assertEqual(source_url('data/raw/paper.pdf'),'/sources/'+self.identifier)

    def test_unregistered_files_and_internal_paths_denied(self):
        (self.root/'data/raw/secret.pdf').write_bytes(b'%PDF-1.4\nprivate')
        self.assertIsNone(self.catalog.read(source_id('data/raw/secret.pdf')))
        for path in ('data/docs_db/secret','data/qdrant_db/database','data/raw/../secret.pdf','/data/raw/%2e%2e/secret.pdf','/data/raw/%252e%252e/secret.pdf','uploads/backend/private.jpg','file:///etc/passwd'):
            with self.subTest(path=path):self.assertIsNone(self.catalog.legacy_id(path))
        self.assertIsNone(SourceCatalog(self.root,self.root/'absent.json').read(self.identifier))

    def test_manifest_cannot_publish_internal_storage(self):
        manifest=self.root/'invalid.json'
        manifest.write_text(json.dumps({'version':1,'sources':{'s_'+'0'*64:{'path':'data/docs_db/secret.pdf','sha256':'0'*64}}}))
        with self.assertRaises(ValueError):SourceCatalog(self.root,manifest)

    def test_hash_changes_and_symlinks_denied(self):
        self.path.write_bytes(b'%PDF-1.4\nchanged')
        self.assertIsNone(self.catalog.read(self.identifier))
        self.path.unlink(); target=self.root/'outside.pdf';target.write_bytes(b'%PDF-1.4\nregistered paper')
        self.path.symlink_to(target)
        self.assertIsNone(self.catalog.read(self.identifier))

    def test_private_owner_expiry_and_snapshot(self):
        clock=[10]
        store=PrivateArtifacts(ttl=5,clock=lambda:clock[0])
        identifier=store.publish_png(PNG,'session-A').split('/')[-1]
        self.assertEqual(store.read(identifier,'session-A').content,PNG)
        self.assertIsNone(store.read(identifier,None))
        self.assertIsNone(store.read(identifier,'session-B'))
        self.assertIsNone(store.read(identifier,'é'))
        store.publish_png(PNG+b'new','session-B')
        self.assertEqual(store.read(identifier,'session-A').content,PNG)
        clock[0]=15
        self.assertIsNone(store.read(identifier,'session-A'))

    def test_artifact_capacity_and_content_limits(self):
        store=PrivateArtifacts(max_entries=1)
        first=store.publish_png(PNG,'A').split('/')[-1]
        second=store.publish_png(PNG,'A').split('/')[-1]
        self.assertIsNone(store.read(first,'A'))
        self.assertIsNotNone(store.read(second,'A'))
        with self.assertRaises(ValueError):store.publish_png(b'<script>bad</script>','A')
        with self.assertRaises(ValueError):store.publish_png(PNG,'')


class SourceHttpTests(unittest.TestCase):
    setUp = SourceAccessTests.setUp

    def test_http_allows_registered_source_only_and_checks_cookie(self):
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest('requires lightweight API dependencies')
        from source_access import create_source_router
        store=PrivateArtifacts()
        private_url=store.publish_png(PNG,'session-A')
        app=FastAPI();app.include_router(create_source_router(self.catalog,store))
        with TestClient(app) as client:
            response=client.get('/sources/'+self.identifier)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.content,self.path.read_bytes())
            self.assertIn('no-store',response.headers['cache-control'])
            self.assertEqual(client.get('/data/raw/paper.pdf').status_code,200)
            for path in ('/data/docs_db/secret','/data/qdrant_db/database','/data/raw/secret.pdf','/uploads/backend/private.jpg','/data/raw/%2e%2e/docs_db/secret','/sources'):
                self.assertEqual(client.get(path).status_code,404,path)
            self.assertEqual(client.get(private_url).status_code,404)
            client.cookies.set('session_id','session-B')
            self.assertEqual(client.get(private_url).status_code,404)
            client.cookies.set('session_id','session-A')
            self.assertEqual(client.get(private_url).content,PNG)
            self.path.write_bytes(b'%PDF-1.4\nchanged')
            self.assertEqual(client.get('/sources/'+self.identifier).status_code,404)


if __name__ == '__main__':
    unittest.main()
