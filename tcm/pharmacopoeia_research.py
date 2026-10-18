"""Read-only lexical research retrieval of machine-checked source excerpts."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import unicodedata
from tcm.pharmacopoeia import FIELDS


def normalize(text):
    return ''.join(unicodedata.normalize('NFKC', text).split()).casefold()


def build_index(path, batch, batch_sha256):
    path = Path(path)
    if path.exists(): raise FileExistsError('Use a fresh index path')
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute('INSERT INTO metadata VALUES (?,?)', ('batch_sha256', batch_sha256))
        db.execute('CREATE TABLE excerpts(id TEXT PRIMARY KEY,case_id TEXT,name TEXT,field TEXT,body TEXT,norm_text TEXT,payload TEXT)')
        db.execute("CREATE VIRTUAL TABLE search USING fts5(norm_text,content='excerpts',content_rowid='rowid',tokenize='trigram')")
        for field in batch['fields']:
            if not field['eligible_for_research_preview']: continue
            if field['machine_status'] != 'machine_checks_passed' or field['allowed_use'] != 'unreviewed_research_source_excerpt_only':
                raise ValueError('Ineligible field cannot enter research index')
            payload = {key: field[key] for key in ('id','case_id','name_zh_candidate','field','machine_status',
                'human_review','human_signoff','allowed_use','selected_engine','excerpt','previews','reasons')}
            db.execute('INSERT INTO excerpts VALUES (?,?,?,?,?,?,?)', (field['id'], field['case_id'],
                field['name_zh_candidate'], field['field'], field['excerpt'], normalize(field['excerpt']),
                json.dumps(payload, ensure_ascii=False)))
        db.execute("INSERT INTO search(search) VALUES ('rebuild')")
        db.commit()


def search_index(path, query='', herb='', field='', limit=10):
    if type(limit) is not int or not 1 <= limit <= 30: raise ValueError('limit must be 1..30')
    if not all(isinstance(v, str) and len(v) <= 200 for v in (query, herb, field)):
        raise ValueError('Query or filter too long')
    if not query.strip() and not herb.strip() and not field.strip():
        raise ValueError('Provide a query or filter')
    if field and field not in (*FIELDS.values(), 'pinyin', 'medicinal_part', 'latin_drug_name'):
        raise ValueError('Unknown field filter')
    uri = Path(path).resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as lookup:
        exact_herb = bool(query.strip()) and lookup.execute(
            'SELECT 1 FROM excerpts WHERE name=? OR lower(case_id)=? LIMIT 1',
            (query.strip(), normalize(query))).fetchone() is not None
    q = normalize(query); terms=[]; args=[]; join=''; mode='filtered'
    if q:
        if exact_herb:
            terms.append('(e.name=? OR lower(e.case_id)=?)'); args.extend([query.strip(), q]); mode='exact_herb_filter'
        elif len(q) >= 3:
            join = ' JOIN search ON search.rowid=e.rowid'
            terms.append('search MATCH ?'); args.append('"'+q.replace('"','""')+'"'); mode='fts5_trigram'
        else:
            terms.append('instr(e.norm_text,?)>0'); args.append(q); mode='short_query_substring'
    if herb.strip():
        terms.append('(e.case_id=? OR e.name=?)'); args.extend([herb.strip(), herb.strip()])
    if field.strip(): terms.append('e.field=?'); args.append(field.strip())
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        total = db.execute('SELECT count(*) FROM excerpts e'+join+' WHERE '+' AND '.join(terms), args).fetchone()[0]
        rows = db.execute('SELECT e.payload FROM excerpts e'+join+' WHERE '+' AND '.join(terms)+
                          ' ORDER BY e.case_id,e.field LIMIT ?', args+[limit]).fetchall()
        batch_sha256 = db.execute("SELECT value FROM metadata WHERE key='batch_sha256'").fetchone()[0]
    return {'status': 'research_excerpts' if rows else 'no_eligible_excerpt', 'human_signoff': False,
            'query_mode': mode, 'batch_sha256': batch_sha256, 'total': total,
            'results': [json.loads(row[0]) for row in rows],
            'notice': '仅检索未人工审核的原文候选，不生成诊疗或用药结论；未命中不表示原书没有记载。'}
