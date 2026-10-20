"""Read-only reverse target/ingredient lookup from a fingerprint-bound audited catalog."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3


def connect(path):
    con = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    try:
        meta = {r['key']: json.loads(r['value']) for r in con.execute('SELECT key,value FROM metadata')}
        if meta.get('kind') != 'etcm_target_ingredients_v1' or meta.get('build_complete') is not True:
            raise ValueError('Incomplete or incompatible target catalog')
    except Exception:
        con.close(); raise
    return con, meta


def check_binding(meta, enrichment):
    from tcm.etcm_enrichment import connect as herb_connect
    with closing(herb_connect(enrichment)) as con:
        source = {r['key']: json.loads(r['value']) for r in con.execute('SELECT key,value FROM metadata')}
    if (meta['dataset_version'] != source['dataset_version'] or
            meta['herb_source_fingerprint'] != source['source_fingerprint']):
        raise ValueError('Target catalog differs from herb snapshot')


def recognizes_gene(value):
    """Only disambiguate uppercase bare gene symbols when a local catalog knows them."""
    if not os.getenv('ETCM_TARGET_DB') or not re.fullmatch(r'[A-Z][A-Z.-]{1,20}', value):
        return False
    try:
        con, _ = connect(os.environ['ETCM_TARGET_DB'])
        with closing(con):
            return con.execute('SELECT 1 FROM targets WHERE gene_key=? LIMIT 1', (value,)).fetchone() is not None
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return False


def lookup(path, token, *, page=1):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,31}', token) or type(page) is not int or not 1 <= page <= 1000:
        raise ValueError('Use one explicit gene symbol or UniProt, and a bounded page')
    con, meta = connect(path)
    with closing(con):
        con.execute('BEGIN')
        key = token.upper()
        rows = con.execute('SELECT * FROM targets WHERE uniprot=? ORDER BY uniprot', (key,)).fetchall()
        if not rows:
            rows = con.execute('SELECT * FROM targets WHERE gene_key=? ORDER BY uniprot', (key,)).fetchall()
        options = [{k: r[k] for k in ('uniprot', 'gene', 'name', 'row_count', 'source_file', 'source_file_sha256')} for r in rows[:20]]
        result = {'kind': 'target_ingredient', 'query': token, 'page': page,
                  'options': options, 'options_truncated': len(rows) > 20,
                  'dataset_version': meta['dataset_version'], 'coverage_targets': meta['targets'],
                  'reference_target_groups': meta['reference_target_groups']}
        if len(rows) != 1:
            result['resolution_status'] = 'ambiguous_target' if rows else 'not_covered'
            return result
        target = rows[0]
        offset = (page-1)*20
        data = con.execute('SELECT document FROM ingredient_rows WHERE uniprot=? ORDER BY ordinal LIMIT 20 OFFSET ?',
                           (target['uniprot'], offset)).fetchall()
        result.update(resolution_status='source_records', target=options[0],
                      data={'total': target['row_count'], 'offset': offset, 'limit': 20,
                            'items': [json.loads(r[0]) for r in data],
                            'next_offset': offset+20 if offset+20 < target['row_count'] else None})
        return result


def execute(request):
    result = {'topic': 'etcm_relations', 'llm_calls': 0, 'network_calls': 0, 'status': 'ok'}
    path, enrichment = os.getenv('ETCM_TARGET_DB'), os.getenv('ETCM_ENRICHMENT_DB')
    if not path or not enrichment:
        return {**result, 'status': 'unavailable', 'message': '本地靶点成分目录未启用，不改用模型或联网补答。'}
    try:
        con, meta = connect(path); con.close()
        check_binding(meta, enrichment)
        data = lookup(path, request['entities'][0], page=request['page'])
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return {**result, 'status': 'unavailable', 'message': '靶点成分目录不可用、未完成、查询参数无效或与药材快照不一致，未补猜关系。'}
    result['relations'] = data
    if data['resolution_status'] == 'ambiguous_target':
        return {**result, 'status': 'clarification_needed', 'message': '此基因符号对应多个来源UniProt，请选择本次查询的标识，不自动合并。'}
    if data['resolution_status'] == 'not_covered':
        return {**result, 'status': 'clarification_needed', 'message': '当前通过来源一致性检查的目录未覆盖此靶点。它可能尚未收录、被排除或标识不匹配；不表示该靶点没有成分关联。请提供明确的基因符号或UniProt。'}
    result['message'] = (f"该靶点来源直接列出{data['data']['total']}条成分关联，每页最多20条。"
                         '仅表示数据库收录，未核验化学身份、实验结合或作用机制；保留原始行及抓取状态。'
                         '本会话可回看已选靶点来源，不会将靶点当作药材。')
    return result
