"""Reverse source-declared UniProt edges; counts are herb records, not drug effects."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

from tcm.etcm_enrichment import connect
from tcm.target_ingredients import connect as target_connect, check_binding, lookup


def query(enrichment, targets, database, token, *, page=1):
    from tcm.herbs import SCHEMA_VERSION, _resolve_dataset_version, lookup as herb_lookup
    target_con, meta = target_connect(targets)
    target_con.close()
    check_binding(meta, enrichment)
    resolved = lookup(targets, token, page=page)
    result = {k: v for k, v in resolved.items() if k != 'data'}
    result['kind'] = 'target_herb'
    if resolved['resolution_status'] != 'source_records':
        return result
    version = meta['dataset_version']
    if os.getenv('TCM_DATASET_VERSION') and os.environ['TCM_DATASET_VERSION'] != version:
        raise ValueError('Selected herb version differs')
    with closing(connect(enrichment)) as con, closing(sqlite3.connect(
            Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as base:
        con.execute('BEGIN'); base.execute('PRAGMA query_only=ON'); base.execute('BEGIN')
        if base.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise ValueError('Unknown herb schema')
        selected, exists = _resolve_dataset_version(base, os.getenv('TCM_DATASET_VERSION') or None)
        if not exists or selected != version:
            raise ValueError('Current herb snapshot differs')
        # Check the whole count denominator, including records outside this page.
        current = dict(base.execute("SELECT source_record_id,sha256 FROM records WHERE dataset_version=? AND state='valid'", (version,)))
        indexed = dict(con.execute('SELECT source_record_id,file_sha256 FROM herbs WHERE dataset_version=?', (version,)))
        if not current or current != indexed:
            raise ValueError('Reverse index differs from the current herb snapshot')
        uniprot = result['target']['uniprot']
        nodes = con.execute("SELECT node_id FROM nodes WHERE kind='target' AND identifier=? "
                            "AND identity_status LIKE 'source_declared_uniprot_not_externally_verified%' LIMIT 2", (uniprot,)).fetchall()
        if len(nodes) > 1:
            raise ValueError('UniProt grouping is not unique in this catalog')
        # Resolve once, then use the existing node/edge index rather than scan all edges.
        source = (" FROM edges e JOIN herbs h ON h.source_record_id=e.herb_id "
                  "WHERE e.kind='target' AND e.node_id=? AND h.dataset_version=?")
        params = (nodes[0][0] if nodes else None, version)
        total = con.execute('SELECT COUNT(DISTINCT e.herb_id)' + source, params).fetchone()[0]
        rows = con.execute('SELECT e.herb_id,COUNT(*) AS row_count' + source +
                           ' GROUP BY e.herb_id ORDER BY e.herb_id LIMIT 20 OFFSET ?', (*params, (page-1)*20)).fetchall()
        items = []
        for row in rows:
            found = herb_lookup(database, source_record_id=row['herb_id'], dataset_version=version)
            if found['status'] != 'ok':
                raise ValueError('Herb record is unavailable')
            record = found['records'][0]
            if record['source']['file_sha256'] != indexed[row['herb_id']]:
                raise ValueError('Herb source changed')
            witnesses = con.execute('SELECT e.edge_id,e.source_pointer,e.raw_row,e.capture_status' + source +
                                   ' AND e.herb_id=? ORDER BY e.source_pointer,e.edge_id LIMIT 3', (*params, row['herb_id'])).fetchall()
            document = json.loads(con.execute('SELECT document FROM herbs WHERE source_record_id=?', (row['herb_id'],)).fetchone()[0])
            summary = document.get('complete_fetch_summary')
            capture = [r for r in summary if isinstance(r, dict) and r.get('section') == 'related_table'
                       and r.get('type') == 'target'] if isinstance(summary, list) else []
            items.append({'source_record_id': row['herb_id'], 'name': record['fields']['name_pinyin']['value'],
                          'medicinal_part': record['fields']['medicinal_part']['value'], 'source': record['source'],
                          'source_row_count': row['row_count'], 'witnesses_truncated': row['row_count'] > 3,
                          'source_capture_status': capture,
                          'witnesses': [{**dict(w), 'raw_row': json.loads(w['raw_row']),
                                         'capture_status': json.loads(w['capture_status'])} for w in witnesses]})
        offset = (page-1)*20
        result['data'] = {'total': total, 'offset': offset, 'limit': 20, 'items': items,
                          'next_offset': offset+20 if offset+20 < total else None,
                          'count_unit': 'distinct_herb_source_records',
                          'coverage': {'herb_source_records': len(indexed), 'audited_target_files': meta['targets'],
                                       'reference_target_groups': meta['reference_target_groups']}}
        return result


def execute(request):
    result = {'topic': 'etcm_relations', 'status': 'ok', 'llm_calls': 0, 'network_calls': 0}
    enrichment, targets = os.getenv('ETCM_ENRICHMENT_DB'), os.getenv('ETCM_TARGET_DB')
    if not enrichment or not targets:
        return {**result, 'status': 'unavailable', 'message': '本地靶点与药材关联目录未启用，不改用模型或联网补答。'}
    try:
        data = query(enrichment, targets, os.getenv('TCM_DB') or Path(__file__).resolve().parents[1]/'var/tcm/herbs.sqlite3',
                     request['entities'][0], page=request['page'])
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        return {**result, 'status': 'unavailable', 'message': '靶点反查目录、药材来源或版本不可用/不一致，未补猜关联。'}
    result['relations'] = data
    if data['resolution_status'] == 'ambiguous_target':
        return {**result, 'status': 'clarification_needed', 'message': '此基因符号对应多个来源UniProt，请选择明确标识后反查；不合并不同靶点。'}
    if data['resolution_status'] == 'not_covered':
        return {**result, 'status': 'clarification_needed', 'message': '已核对靶点目录尚未覆盖此标识；未覆盖不表示没有药材关联。请使用已收录的基因符号或UniProt。'}
    result['message'] = (f"当前药材来源中有{data['data']['total']}条不同药材记录列出该UniProt，每页最多20条。"
                         '记录数不是去重药材种数；同名不同来源保留，每条最多3行原始见证。'
                         '这是来源关联，不是药效排名、治疗推荐或作用机制证明；未返回不代表没有关系，抓取完整性未保证。')
    return result
