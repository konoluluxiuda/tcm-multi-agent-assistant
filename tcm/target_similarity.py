"""Read-only source target sets and deterministic, provenance-bearing rankings."""
from contextlib import closing
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from time import monotonic

from tcm.etcm_enrichment import connect
from tcm.herbs import SCHEMA_VERSION

VERSION = 'source_target_sets_v1'


class SnapshotChanged(ValueError):pass


def file_token(path):
    parts=[]
    for p in (Path(path), Path(str(path)+'-wal')):
        if p.exists():
            s=p.stat();parts.append(None if str(p).endswith('-wal') and s.st_size==0 else (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns))
        else:parts.append(None)
    return parts


def rank_sets(query_id, sets, *, metric='jaccard', top_k=10):
    """Only positive-overlap peers with observed targets; ties are reproducible."""
    if metric not in ('jaccard','common_count') or type(top_k) is not int or not 1<=top_k<=20:
        raise ValueError('Invalid ranking parameters')
    query=sets[query_id]
    if not query:return []
    rows=[]
    for identifier, targets in sets.items():
        if identifier==query_id or not targets:continue
        shared=query & targets
        if not shared:continue
        union=query | targets
        rows.append({'source_record_id':identifier,'shared_ids':sorted(shared),
                     'query_only_ids':sorted(query-targets),'candidate_only_ids':sorted(targets-query),
                     'shared_count':len(shared),'union_count':len(union),'target_count':len(targets),
                     'jaccard':float(Fraction(len(shared),len(union)))})
    def key(row):
        score=Fraction(row['shared_count'],row['union_count'])
        return ((-score,-row['shared_count']) if metric=='jaccard' else (-row['shared_count'],-score))+(row['source_record_id'],)
    return sorted(rows,key=key)[:top_k]


def query(enrichment, database, record, version, *, tool, metric='jaccard', top_k=10, expected_revision=None):
    if tool not in ('herb_target_profile','herb_target_similarity'):raise ValueError('Unknown analysis')
    before=[file_token(p) for p in (enrichment,database)]
    started=monotonic()
    with closing(connect(enrichment)) as side, closing(sqlite3.connect(
            Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as base:
        # Bound database work without a cache that can silently outlive a snapshot.
        for con in (side,base):
            con.set_progress_handler(lambda: int(monotonic()-started>10),10000)
            con.execute('PRAGMA query_only=ON');con.execute('BEGIN')
        if base.execute('PRAGMA user_version').fetchone()[0]!=SCHEMA_VERSION:raise ValueError('Unknown schema')
        meta={r['key']:json.loads(r['value']) for r in side.execute('SELECT key,value FROM metadata')}
        if meta['dataset_version']!=version:raise SnapshotChanged('Version differs')
        originals={}
        for sid,sha,name,source in base.execute("SELECT source_record_id,sha256,json_extract(document,'$.fields.name_pinyin.value'),json_extract(document,'$.source') FROM records WHERE dataset_version=? AND state='valid'",(version,)):
            originals[sid]={'source_record_id':sid,'name':name or sid,'source':json.loads(source),'sha':sha}
        indexed={r['source_record_id']:r['file_sha256'] for r in side.execute('SELECT source_record_id,file_sha256 FROM herbs WHERE dataset_version=?',(version,))}
        if not originals or {k:v['sha'] for k,v in originals.items()}!=indexed:
            raise SnapshotChanged('Catalog and base source records differ')
        sid=record['source_record_id']
        if indexed.get(sid)!=record['source']['file_sha256']:raise SnapshotChanged('Selected source differs')
        revision=hashlib.sha256(json.dumps([VERSION,version,meta['source_fingerprint'],before],sort_keys=True).encode()).hexdigest()
        if expected_revision is not None and expected_revision!=revision:raise SnapshotChanged('Analysis changed')
        names={}
        for row in side.execute('SELECT DISTINCT herb_id,name_zh FROM name_candidates'):
            names.setdefault(row['herb_id'],set()).add(row['name_zh'])
        sets={k:set() for k in originals};raw_counts={k:0 for k in originals};eligible_counts={k:0 for k in originals}
        for herb,identifier,count in side.execute("SELECT e.herb_id,n.identifier,COUNT(*) FROM edges e JOIN nodes n ON n.node_id=e.node_id WHERE e.kind='target' AND n.kind='target' AND n.identity_status LIKE 'source_declared_uniprot_not_externally_verified%' GROUP BY e.herb_id,n.identifier"):
            if herb not in sets:raise SnapshotChanged('Unknown source edge')
            if isinstance(identifier,str) and re.fullmatch(r'[A-Z0-9]{6}(?:[A-Z0-9]{4})?',identifier):
                sets[herb].add(identifier);eligible_counts[herb]+=count
        for herb,count in side.execute("SELECT herb_id,COUNT(*) FROM edges WHERE kind='target' GROUP BY herb_id"):
            if herb not in sets:raise SnapshotChanged('Unknown target source')
            raw_counts[herb]=count
        def source(identifier):
            r=originals[identifier]
            candidate=names.get(identifier,set())
            stored=side.execute("SELECT json_extract(document,'$.complete_fetch_summary') FROM herbs WHERE source_record_id=?",(identifier,)).fetchone()[0]
            summary=json.loads(stored) if stored else []
            capture=[x for x in summary if isinstance(x,dict) and x.get('section')=='related_table' and x.get('type')=='target'] if isinstance(summary,list) else []
            return {'source_record_id':identifier,'name':next(iter(candidate)) if len(candidate)==1 else r['name'],
                    'name_pinyin':r['name'],'source':r['source'],'capture_status':capture,
                    'name_identity_verified':False}
        profile={'source':source(sid),'target_ids':sorted(sets[sid]),'target_count':len(sets[sid]),
                 'source_row_count':raw_counts[sid], 'unidentified_rows':raw_counts[sid]-eligible_counts[sid],
                 'duplicate_identifier_rows':eligible_counts[sid]-len(sets[sid])}
        coverage={'source_records':len(sets),'with_targets':sum(bool(s) for s in sets.values()),
                  'without_targets':sum(not s for s in sets.values()),
                  'distinct_identifiers':len(set().union(*sets.values()))}
        output={'algorithm_version':VERSION,'dataset_version':version,'catalog_revision':revision,
                'profile':profile,'coverage':coverage}
        if tool=='herb_target_similarity':
            rows=rank_sets(sid,sets,metric=metric,top_k=top_k)
            output.update(metric=metric,top_k=top_k,
                          eligible_peers=sum(bool(s) for k,s in sets.items() if k!=sid),
                          overlapping_peers=sum(bool(sets[sid]&s) for k,s in sets.items() if k!=sid),
                          items=[{**r,'source':source(r['source_record_id'])} for r in rows])
    if before!=[file_token(p) for p in (enrichment,database)]:raise SnapshotChanged('Catalog changed during analysis')
    return output
