"""Durable experiment admissions. Unknown sends are charged and never repeated."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Ledger:
    def __init__(self,path,budget):
        self.path=Path(path);self.budget=budget
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.db() as con:
            con.executescript('''CREATE TABLE IF NOT EXISTS cases(id TEXT,policy TEXT,status TEXT,result TEXT,PRIMARY KEY(id,policy));
                CREATE TABLE IF NOT EXISTS dispatches(id INTEGER PRIMARY KEY,scenario TEXT,policy TEXT,turn TEXT,status TEXT,http INTEGER,usage TEXT,created REAL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);''')
            encoded=json.dumps(budget,sort_keys=True)
            old=con.execute("SELECT value FROM settings WHERE key='budget'").fetchone()
            if old and old[0]!=encoded: raise ValueError('Ledger budget changed')
            con.execute("INSERT OR IGNORE INTO settings VALUES('budget',?)",(encoded,))
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        con=sqlite3.connect(self.path,timeout=10)
        try:
            with con: yield con
        finally: con.close()

    def claim(self,scenario,policy):
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT 1 FROM cases WHERE id=? AND policy=?',(scenario,policy)).fetchone():return False
            con.execute('INSERT INTO cases VALUES(?,?,?,NULL)',(scenario,policy,'started'))
        return True

    def finish(self,scenario,policy,result):
        with self.db() as con:
            n=con.execute("UPDATE cases SET status='finished',result=? WHERE id=? AND policy=? AND status='started'",(json.dumps(result,ensure_ascii=False),scenario,policy)).rowcount
            if n!=1:raise ValueError('Combination is not pending; refusing overwrite')

    def stopped(self):
        with self.db() as con:
            return bool(con.execute("SELECT 1 FROM dispatches WHERE status!='received' OR http>=400 LIMIT 1").fetchone())

    def reserve(self,scenario,policy,turn):
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT 1 FROM dispatches WHERE status!='received' OR http>=400 LIMIT 1").fetchone():raise RuntimeError('Provider stopped after unknown/error')
            total=con.execute('SELECT count(*) FROM dispatches').fetchone()[0]
            used=con.execute('SELECT count(*) FROM dispatches WHERE policy=?',(policy,)).fetchone()[0]
            local=con.execute('SELECT count(*) FROM dispatches WHERE scenario=? AND policy=? AND turn=?',(scenario,policy,turn)).fetchone()[0]
            if total>=self.budget['total'] or used>=self.budget[policy] or local>={'R':0,'G':1,'V':2}[policy]:raise RuntimeError('Dispatch budget exhausted')
            return con.execute('INSERT INTO dispatches(scenario,policy,turn,status,created) VALUES(?,?,?,?,?)',(scenario,policy,turn,'unknown',time.time())).lastrowid

    def received(self,identifier,http,usage):
        with self.db() as con:
            n=con.execute("UPDATE dispatches SET status='received',http=?,usage=? WHERE id=? AND status='unknown'",(http,json.dumps(usage) if usage is not None else None,identifier)).rowcount
            if n!=1:raise ValueError('Dispatch receipt already committed')

    def results(self):
        with self.db() as con:
            return [json.loads(row[0]) if row[0] else {'scenario_id':row[1],'policy':row[2],'status':'unknown','steps':[],'error':'interrupted combination; never auto-retry'} for row in con.execute('SELECT result,id,policy FROM cases ORDER BY rowid')]

    def dispatches(self):
        with self.db() as con:
            con.row_factory=sqlite3.Row
            return [dict(r) for r in con.execute('SELECT * FROM dispatches ORDER BY id')]
