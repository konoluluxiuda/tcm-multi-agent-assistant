"""Local literature ledger: unique PMID records plus immutable search snapshots."""
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4
from tcm.literature_evidence import annotate_batch, doi_key


class LiteratureStore:
    def __init__(self, path):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError:
            pass
        self.migration_backup = None
        self.db = sqlite3.connect(path, timeout=10)
        try:
            self.db.execute('PRAGMA foreign_keys=ON')
            # Hold a writer reservation through validation/backup/upgrade.
            self.db.execute('BEGIN IMMEDIATE')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError('Unsupported literature schema version')
            if version == 0:
                if self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError('Refusing to initialize a nonempty unversioned database')
                self.db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                self.db.execute("INSERT INTO metadata VALUES('store_kind', 'pubmed_literature_v1')")
                self.db.execute('CREATE TABLE articles (pmid TEXT PRIMARY KEY, doi TEXT, payload TEXT NOT NULL, doi_key TEXT)')
                self.db.execute('CREATE INDEX articles_doi ON articles(doi)')
                self.db.execute('CREATE INDEX articles_doi_key ON articles(doi_key)')
                self.db.execute('CREATE TABLE search_runs (id TEXT PRIMARY KEY, query TEXT NOT NULL, '
                                'retrieved_at TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)')
                self.db.execute('CREATE INDEX runs_date ON search_runs(retrieved_at)')
                self.db.execute('CREATE TABLE run_articles (run_id TEXT NOT NULL REFERENCES search_runs(id), '
                                'pmid TEXT NOT NULL REFERENCES articles(pmid), rank INTEGER NOT NULL CHECK(rank > 0), '
                                'payload TEXT NOT NULL, PRIMARY KEY(run_id, pmid), UNIQUE(run_id, rank))')
                self.db.execute('PRAGMA user_version=2')
            else:
                tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {'metadata', 'articles', 'search_runs', 'run_articles'} <= tables:
                    raise ValueError('Not a literature database')
                marker = self.db.execute("SELECT value FROM metadata WHERE key='store_kind'").fetchone()
                if marker != ('pubmed_literature_v1',):
                    raise ValueError('Not a literature database')
                if version == 1:
                    self.migration_backup = self._upgrade(path)
                elif 'doi_key' not in {row[1] for row in self.db.execute('PRAGMA table_info(articles)')}:
                    raise ValueError('Invalid literature schema')
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.db.close()
            raise

    def _upgrade(self, path):
        directory = path.parent/'migration_backups'
        directory.mkdir(mode=0o700, exist_ok=True)
        backup = directory/(path.stem+'-v1-'+uuid4().hex+'.sqlite3')
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        reader = dest = None
        try:
            # A separate reader sees the committed pre-upgrade database while
            # this connection's BEGIN IMMEDIATE prevents other writers.
            reader = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=10)
            dest = sqlite3.connect(backup)
            started = time.monotonic()
            def progress(*_):
                if time.monotonic()-started > 30: raise ValueError('Migration backup timeout')
            reader.backup(dest, pages=256, progress=progress, sleep=.05)
            dest.execute('PRAGMA journal_mode=DELETE')
            if dest.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Migration backup integrity failed')
            dest.close(); dest = None
            with backup.open('rb') as stream: os.fsync(stream.fileno())
        except Exception:
            if dest is not None: dest.close(); dest = None
            backup.unlink(missing_ok=True)
            raise
        finally:
            if reader is not None: reader.close()
        # All DDL/backfill/version changes roll back together on any failure.
        # The verified old backup remains available even if the upgrade fails.
        self.db.execute('ALTER TABLE articles ADD COLUMN doi_key TEXT')
        cursor = self.db.execute('SELECT pmid,payload FROM articles')
        while rows := cursor.fetchmany(128):
            for pmid, payload in rows:
                self.db.execute('UPDATE articles SET doi_key=? WHERE pmid=?', (doi_key(json.loads(payload)), pmid))
        self.db.execute('CREATE INDEX articles_doi_key ON articles(doi_key)')
        self.db.execute('PRAGMA user_version=2')
        return backup

    def save(self, result):
        run_id = uuid4().hex
        encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True)
        with self.db:
            # Serialize catalog lookup and insert so another writer cannot add
            # an unseen DOI peer between classification and commit.
            self.db.execute('BEGIN IMMEDIATE')
            catalog, previous = [], []
            for article in result['articles']:
                key = doi_key(article)
                if key:
                    catalog.extend(json.loads(row[0]) for row in self.db.execute(
                        'SELECT payload FROM articles WHERE doi_key=?', (key,)))
                row = self.db.execute('SELECT payload FROM articles WHERE pmid=?', (article['pmid'],)).fetchone()
                if row: previous.append(json.loads(row[0]))
            saved = annotate_batch(result, catalog, previous)
            self.db.execute('INSERT INTO search_runs VALUES(?,?,?,?,?)',
                            (run_id, saved['query'], saved['retrieved_at'], saved['status'], encode(saved)))
            for rank, article in enumerate(saved['articles'], 1):
                payload = encode(article)
                self.db.execute('INSERT INTO articles(pmid,doi,payload,doi_key) VALUES(?,?,?,?) ON CONFLICT(pmid) DO UPDATE SET '
                                'doi=excluded.doi, payload=excluded.payload, doi_key=excluded.doi_key',
                                (article['pmid'], article['doi'], payload, doi_key(article)))
                self.db.execute('INSERT INTO run_articles VALUES(?,?,?,?)', (run_id, article['pmid'], rank, payload))
        return run_id

    def get(self, run_id):
        row = self.db.execute('SELECT payload FROM search_runs WHERE id=?', (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self):
        self.db.close()
