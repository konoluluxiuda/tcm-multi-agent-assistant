"""Explicit user research preferences. Never a source of medicinal facts."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from pydantic import BaseModel, Field, field_validator


class ProjectData(BaseModel):
    model_config = {'extra': 'forbid'}
    title: str = Field(min_length=1, max_length=80)
    herbs: list[str] = Field(min_length=1, max_length=2)
    question: str = Field(min_length=1, max_length=400)

    @field_validator('title', 'question')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Blank project field')
        return value.strip()

    @field_validator('herbs')
    @classmethod
    def names(cls, values):
        values = [v.strip() for v in values]
        if any(not v or len(v) > 80 for v in values) or len(set(values)) != len(values):
            raise ValueError('Use one or two distinct herb names')
        return values


class ProjectConflict(Exception):
    pass


class ProjectStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            marker = db.execute('PRAGMA application_id').fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if (version, marker) not in ((0, 0), (1, 1413696848)) or (version == 0 and tables):
                raise sqlite3.DatabaseError('Not a supported project memory database')
            db.execute('PRAGMA user_version=1')
            db.execute('PRAGMA application_id=1413696848')
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL)')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def document(row):
        return dict(id=row[0], revision=row[1], updated=row[2], **json.loads(row[3]))

    def list(self, owner):
        with self.connection() as db:
            return [self.document(r) for r in db.execute('SELECT id,revision,updated,payload FROM projects WHERE owner=? ORDER BY updated DESC,id', (owner,))]

    def get(self, owner, identifier):
        with self.connection() as db:
            row = db.execute('SELECT id,revision,updated,payload FROM projects WHERE owner=? AND id=?', (owner, identifier)).fetchone()
            if row is None:
                raise KeyError(identifier)
            return self.document(row)

    def save(self, owner, data, *, identifier=None, revision=None):
        data = ProjectData.model_validate(data).model_dump()
        raw = json.dumps(data, ensure_ascii=False)
        now = time.time()
        with self.connection() as db:
            if identifier is None:
                if db.execute('SELECT count(*) FROM projects WHERE owner=?', (owner,)).fetchone()[0] >= 20:
                    raise ProjectConflict('最多保存20个项目，请先删除不再使用的项目。')
                identifier = uuid.uuid4().hex
                revision = 1
                db.execute('INSERT INTO projects VALUES (?,?,?,?,?)', (identifier, owner, revision, now, raw))
            else:
                row = db.execute('SELECT revision FROM projects WHERE owner=? AND id=?', (owner, identifier)).fetchone()
                if row is None:
                    raise KeyError(identifier)
                if row[0] != revision:
                    raise ProjectConflict('项目已在其他页面修改，请刷新后再编辑。')
                revision += 1
                db.execute('UPDATE projects SET revision=?,updated=?,payload=? WHERE owner=? AND id=?', (revision, now, raw, owner, identifier))
        return dict(id=identifier, revision=revision, updated=now, **data)

    def delete(self, owner, identifier, revision):
        with self.connection() as db:
            row = db.execute('SELECT revision FROM projects WHERE owner=? AND id=?', (owner, identifier)).fetchone()
            if row is None:
                raise KeyError(identifier)
            if row[0] != revision:
                raise ProjectConflict('项目已修改，请刷新后再删除。')
            db.execute('DELETE FROM projects WHERE owner=? AND id=?', (owner, identifier))
