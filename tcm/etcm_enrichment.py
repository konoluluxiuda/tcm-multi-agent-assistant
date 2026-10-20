"""Read-only sidecar: translations, name candidates and provenance-bearing source relations."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

KIND_LABELS={'ingredient':'来源收录成分','target':'来源关联靶点','disease':'来源富集疾病',
             'chinese_patent_drug':'关联中成药','traditional_chinese_medicine_formula':'关联传统方剂',
             'reference_pmid':'来源列出PMID','external_reference':'外部数据库记录'}


def connect(path):
    con=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row;con.execute('PRAGMA query_only=ON')
    try:
        meta={r['key']:json.loads(r['value']) for r in con.execute('SELECT key,value FROM metadata')}
        if meta.get('kind')!='etcm_enrichment_v1' or meta.get('build_complete') is not True:
            raise ValueError('Enrichment is incomplete or incompatible')
    except Exception:
        con.close();raise
    return con


def relations(path,record,version,*,kind=None,limit=20,offset=0):
    if kind is not None and kind not in KIND_LABELS:raise ValueError('Unknown relation type')
    if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:raise ValueError('Invalid pagination')
    with closing(connect(path)) as con:
        row=con.execute('SELECT * FROM herbs WHERE source_record_id=? AND dataset_version=?',
                        (record['source_record_id'],version)).fetchone()
        if row is None or row['file_sha256']!=record['source']['file_sha256']:
            raise ValueError('Enrichment source snapshot differs')
        conditions='e.herb_id=?';params=[record['source_record_id']]
        if kind:conditions+=' AND e.kind=?';params.append(kind)
        total=con.execute('SELECT COUNT(*) FROM edges e WHERE '+conditions,params).fetchone()[0]
        rows=con.execute('SELECT e.*,n.name,n.identity_status,n.identifier FROM edges e JOIN nodes n ON n.node_id=e.node_id '
                         'WHERE '+conditions+' ORDER BY e.kind,e.source_pointer,e.edge_id LIMIT ? OFFSET ?',
                         [*params,limit,offset]).fetchall()
        items=[]
        for edge in rows:
            item=dict(edge)
            for key in ('raw_row','capture_status'):item[key]=json.loads(item[key])
            label_key={'ingredient':'Ingredient Name','target':'Target Name','disease':'Disease Name',
                       'chinese_patent_drug':'Formula Name in Chinese Pinyin',
                       'traditional_chinese_medicine_formula':'Formula Name in Chinese Pinyin'}.get(item['kind'],'label')
            source_name=item['raw_row'].get(label_key)
            source_name=source_name[0] if isinstance(source_name,list) and source_name else source_name
            if isinstance(source_name,str):
                if 'multiple_source_labels' in item['identity_status'] or source_name.strip().casefold()!=item['name'].strip().casefold():
                    item['identity_note']='同一分组标识在来源中使用了不同名称，请核对原始行。'
                item['name']=source_name
            item['relation_label']=KIND_LABELS[item['kind']]
            item['file_candidates']=[dict(r) for r in con.execute(
                'SELECT relative_path,status FROM file_candidates WHERE node_id=? ORDER BY relative_path LIMIT 5',(item['node_id'],))]
            item['other_herb_sources']=[dict(r) for r in con.execute(
                "SELECT DISTINCT h.source_record_id,json_extract(h.document,'$.fields.name_pinyin.value') AS name_pinyin "
                'FROM edges e JOIN herbs h ON h.source_record_id=e.herb_id '
                'WHERE e.node_id=? AND e.herb_id<>? ORDER BY h.source_record_id LIMIT 5',
                (item['node_id'],record['source_record_id']))]
            item['other_sources_limit']=5
            items.append(item)
        result = {'status':'source_records','total':total,'returned_count':len(items),'items':items,
                'next_offset':offset+limit if offset+limit<total else None,'limit':limit,'offset':offset,
                'evidence_scope':'database_reported_associations_not_clinical_or_causal_evidence'}
        if kind == 'disease':
            summary = json.loads(row['document']).get('complete_fetch_summary')
            result['source_capture_status'] = [s for s in summary if isinstance(s, dict)
                and s.get('section') == 'related_table' and s.get('type') == 'disease'] if isinstance(summary, list) else []
        return result


def enrich_records(records,version):
    path=os.getenv('ETCM_ENRICHMENT_DB')
    if not path:return records
    try:
        with closing(connect(path)) as con:
            has_comparisons = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='identity_comparisons'").fetchone()
            for record in records:
                row=con.execute('SELECT * FROM herbs WHERE source_record_id=? AND dataset_version=?',
                                (record['source_record_id'],version)).fetchone()
                if row is None or row['file_sha256']!=record['source']['file_sha256']:
                    record['enrichment_status']='source_mismatch';continue
                record['enrichment_status']='available'
                record['name_zh_candidates']=[json.loads(r[0]) for r in con.execute(
                    'SELECT evidence FROM name_candidates WHERE herb_id=? ORDER BY name_zh,entry_id',(record['source_record_id'],))]
                record.pop('identity_comparisons', None)
                if has_comparisons:
                    record['identity_comparisons']=[json.loads(r[0]) for r in con.execute(
                        'SELECT evidence FROM identity_comparisons WHERE herb_id=? ORDER BY entry_id',
                        (record['source_record_id'],))]
                for field in con.execute('SELECT field,translation FROM translations WHERE herb_id=?',(record['source_record_id'],)):
                    if field['field'] in record['fields']:
                        record['fields'][field['field']]['translation']=json.loads(field['translation'])
                record['relation_counts']=[{'kind':r[0],'label':KIND_LABELS.get(r[0],r[0]),'count':r[1]}
                    for r in con.execute('SELECT kind,COUNT(*) FROM edges WHERE herb_id=? GROUP BY kind ORDER BY kind',(record['source_record_id'],))]
    except (OSError,ValueError,sqlite3.Error,KeyError,TypeError):
        # A broken optional sidecar must not make the original source appear translated/linked.
        for record in records:
            record.pop('name_zh_candidates',None);record.pop('relation_counts',None)
            record.pop('identity_comparisons', None)
            for field in record.get('fields',{}).values():field.pop('translation',None)
            record['enrichment_status']='unavailable'
    return records


def chinese_candidates(name,version):
    path=os.getenv('ETCM_ENRICHMENT_DB')
    if not path:return []
    try:
        with closing(connect(path)) as con:
            return [dict(r) for r in con.execute('SELECT DISTINCT h.source_record_id,h.file_sha256 FROM name_candidates c '
                'JOIN herbs h ON h.source_record_id=c.herb_id WHERE c.name_zh=? AND h.dataset_version=? ORDER BY h.source_record_id LIMIT 20',
                (name,version))]
    except (OSError,ValueError,sqlite3.Error,KeyError,TypeError):return []
