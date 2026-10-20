"""Candidate source paths joined by three literal compound fields, never inferred mechanism."""
from contextlib import closing
import json

from tcm.compound_identity import compound_key
from tcm.etcm_enrichment import connect as herb_connect
from tcm.target_ingredients import connect as target_connect, check_binding


def query(enrichment, target_database, record, version, *, page=1):
    if type(page) is not int or not 1 <= page <= 1000:
        raise ValueError('Invalid path page')
    target_con, meta = target_connect(target_database)
    with closing(target_con), closing(herb_connect(enrichment)) as herb_con:
        target_con.execute('BEGIN'); herb_con.execute('BEGIN')
        if meta.get('compound_lookup_version') != 1:
            raise ValueError('Rebuild the target index with compound lookup support')
        check_binding(meta, enrichment)
        source = herb_con.execute('SELECT file_sha256 FROM herbs WHERE source_record_id=? AND dataset_version=?',
                                  (record['source_record_id'], version)).fetchone()
        if source is None or source[0] != record['source']['file_sha256'] or version != meta['dataset_version']:
            raise ValueError('Herb source changed')
        edges = herb_con.execute("SELECT edge_id,source_pointer,raw_row,capture_status FROM edges "
                                 "WHERE herb_id=? AND kind='ingredient' ORDER BY source_pointer,edge_id",
                                 (record['source_record_id'],)).fetchall()
        eligible = []; complete = name_conflicts = 0; identifiers = set()
        for edge in edges:
            raw = json.loads(edge['raw_row']); key = compound_key(raw)
            if key is None:
                continue
            complete += 1
            ids = target_con.execute('SELECT uniprot,COUNT(*) FROM ingredient_rows '
                                     'WHERE match_name=? AND match_formula=? AND match_weight=? GROUP BY uniprot', key).fetchall()
            count = sum(r[1] for r in ids)
            if count:
                identifiers.update(r[0] for r in ids)
                eligible.append((edge, raw, key, count))
            elif target_con.execute('SELECT 1 FROM ingredient_rows WHERE match_name=? LIMIT 1', (key[0],)).fetchone():
                name_conflicts += 1
        total = sum(item[3] for item in eligible)
        offset = (page-1)*20; remaining = 20; skip = offset; items = []
        for edge, raw, key, count in eligible:
            if skip >= count:
                skip -= count
                continue
            rows = target_con.execute('SELECT document FROM ingredient_rows WHERE match_name=? AND match_formula=? AND match_weight=? '
                                      'ORDER BY uniprot,ordinal LIMIT ? OFFSET ?', (*key, remaining, skip)).fetchall()
            for row in rows:
                witness = json.loads(row[0])
                items.append({'ingredient_name': raw['Ingredient Name'][0], 'target': witness['target'],
                              'herb_evidence': {'source_record_id': record['source_record_id'], 'source': record['source'],
                                                'edge_id': edge['edge_id'], 'json_pointer': edge['source_pointer'],
                                                'raw_row': raw, 'capture_status': json.loads(edge['capture_status'])},
                              'target_evidence': witness, 'compound_identity_verified': False,
                              'match_rule': 'literal_name_formula_decimal_weight_v1'})
            remaining -= len(rows); skip = 0
            if remaining == 0:
                break
        return {'total': total, 'offset': offset, 'limit': 20, 'items': items,
                'next_offset': offset+20 if offset+20 < total else None,
                'count_unit': 'pairs_of_source_rows_not_verified_mechanisms',
                'coverage': {'herb_ingredient_source_rows': len(edges), 'complete_identity_rows': complete,
                             'missing_identity_rows': len(edges)-complete, 'matching_herb_rows': len(eligible),
                             'unmatched_complete_rows': complete-len(eligible), 'name_only_conflict_rows': name_conflicts,
                             'distinct_source_uniprot': len(identifiers), 'target_catalog_targets': meta['targets'],
                             'reference_target_groups': meta['reference_target_groups']}}
