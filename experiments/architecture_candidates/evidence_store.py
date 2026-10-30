"""Isolated evidence offload prototype. No route or production runtime integration."""
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4
from tcm.analysis_tools import SPECS
from tcm.task_tools import ToolResult
from tcm.research_evidence_read import read as read_slice


@dataclass(frozen=True)
class Scope:
    owner: str
    run_id: str
    dataset_version: str
    catalog_revision: str


class EvidenceConflict(ValueError):pass


class EvidenceStore:
    MAX_BYTES = 4_000_000
    APP_ID = 1413696850

    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(self.path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        os.close(fd)
        with closing(sqlite3.connect(self.path)) as db,db:
            app=db.execute('PRAGMA application_id').fetchone()[0]
            tables=db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if app not in (0,self.APP_ID) or app==0 and tables:
                raise EvidenceConflict('Wrong evidence database')
            db.execute('''CREATE TABLE IF NOT EXISTS evidence(
                handle TEXT PRIMARY KEY,owner TEXT NOT NULL,run_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,
                UNIQUE(owner,run_id,evidence_id))''')
            db.execute(f'PRAGMA application_id={self.APP_ID}')

    @staticmethod
    def validate_scope(scope):
        if not all(isinstance(v,str) and 0<len(v)<=200 for v in
                   (scope.owner,scope.run_id,scope.dataset_version,scope.catalog_revision)):
            raise EvidenceConflict('Invalid host scope')

    def checked_payload(self,scope,payload):
        self.validate_scope(scope)
        name=payload.get('tool')
        if name not in ('search_disease_sources','disease_gene_profile'):
            raise EvidenceConflict('Unsupported evidence tool')
        result=ToolResult[SPECS[name][1]].model_validate(payload)
        if result.status not in ('ok','empty'):
            raise EvidenceConflict('Failed observations cannot be externalized')
        data=SPECS[name][1].model_validate(result.data).model_dump()
        if (data['dataset_version'],data['catalog_revision'])!=(scope.dataset_version,scope.catalog_revision):
            raise EvidenceConflict('Source version mismatch')
        values=data['items'] if name=='search_disease_sources' else data['target_ids']
        if (result.status=='empty')!= (not values):raise EvidenceConflict('Status mismatch')
        return result.model_dump()

    def put(self,scope,evidence_id,payload):
        if not isinstance(evidence_id,str) or not re.fullmatch(r'obs-[1-9][0-9]*',evidence_id) or len(evidence_id)>32:
            raise EvidenceConflict('Invalid evidence ID')
        # Preserve the exact JSON structure supplied by the host, not Pydantic defaults.
        raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        if len(raw.encode())>self.MAX_BYTES:raise EvidenceConflict('Evidence exceeds storage limit')
        self.checked_payload(scope,payload)
        digest=hashlib.sha256(raw.encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT handle,digest,payload FROM evidence WHERE owner=? AND run_id=? AND evidence_id=?',
                           (scope.owner,scope.run_id,evidence_id)).fetchone()
            if old:
                if old[1]!=digest or old[2]!=raw:raise EvidenceConflict('Evidence ID is immutable')
                handle=old[0]
            else:
                handle=uuid4().hex
                db.execute('INSERT INTO evidence VALUES(?,?,?,?,?,?)',
                           (handle,scope.owner,scope.run_id,evidence_id,digest,raw))
        # Returning a reference happens only after the transaction commits.
        return {'evidence_handle':handle,'source_evidence_id':evidence_id,'content_sha256':digest,
                'tool':payload['tool'],'status':payload['status'],'externalized':True,
                'source_binding':{k:payload['data'][k] for k in
                    ('record_id','query','dataset_version','catalog_revision','page') if k in payload['data']},
                'field_counts':{k:len(v) for k,v in payload['data'].items() if k in ('rows','items','target_ids') and isinstance(v,list)},
                'notice':'Omitted content is available by bounded evidence reads; absence from this index is not absence from source.'}

    def load(self,scope,handle):
        self.validate_scope(scope)
        if not isinstance(handle,str) or not re.fullmatch(r'[0-9a-f]{32}',handle):raise KeyError('Unknown evidence')
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT evidence_id,digest,payload FROM evidence WHERE handle=? AND owner=? AND run_id=?',
                           (handle,scope.owner,scope.run_id)).fetchone()
        if row is None:raise KeyError('Unknown evidence')
        eid,digest,raw=row
        if len(raw.encode())>self.MAX_BYTES or hashlib.sha256(raw.encode()).hexdigest()!=digest:
            raise EvidenceConflict('Evidence integrity mismatch')
        payload=json.loads(raw);self.checked_payload(scope,payload)
        return eid,digest,payload

    def read(self,scope,handle,*,field,offset=0,limit=5):
        eid,digest,payload=self.load(scope,handle)
        result=read_slice({eid:payload},{'evidence_id':eid,'field':field,'offset':offset,'limit':limit})
        if result['status']!='error':
            result['data'].update(evidence_handle=handle,content_sha256=digest)
        return result
