"""Shared owner-bound SQLite receipt: reserve before optional model dispatch."""
import json
from pathlib import Path
import sqlite3
from fastapi import HTTPException


class RequestJournal:
    def __init__(self,path,*,label='研究回答'):
        self.path=Path(path);self.label=label;self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS answers(owner TEXT,key TEXT,signature TEXT,result TEXT,PRIMARY KEY(owner,key))')
        self.path.chmod(0o600)

    def reserve(self,owner,key,signature):
        with sqlite3.connect(self.path,timeout=5) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT signature,result FROM answers WHERE owner=? AND key=?',(owner,key)).fetchone()
            if row:
                if row[0]!=signature:raise HTTPException(409,'此请求编号已用于另一问题或配置。')
                if row[1] is None:raise HTTPException(409,'请求仍在处理或执行状态未知，未重复调用模型。')
                return json.loads(row[1])
            if db.execute('SELECT count(*) FROM answers WHERE owner=?',(owner,)).fetchone()[0]>=100:
                raise HTTPException(429,f'本会话{self.label}记录已达上限。')
            db.execute('INSERT INTO answers VALUES(?,?,?,NULL)',(owner,key,signature))
        return None

    def get(self,owner,key):
        with sqlite3.connect(self.path,timeout=5) as db:
            row=db.execute('SELECT signature,result FROM answers WHERE owner=? AND key=?',(owner,key)).fetchone()
        return None if not row or row[1] is None else json.loads(row[1])

    def complete(self,owner,key,result):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE answers SET result=? WHERE owner=? AND key=?',
                       (json.dumps(result,ensure_ascii=False),owner,key))
