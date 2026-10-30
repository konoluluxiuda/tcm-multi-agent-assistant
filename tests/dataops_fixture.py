"""Small source fixture with agreement, part difference, ambiguity and missing mapping."""
import json
from pathlib import Path
import sqlite3

from scripts.build_mineru_retrieval import build
from tcm.herbs import import_snapshot
from tcm.dataops.data_access import Sources


def make_sources(root):
    root = Path(root)
    raw = root / 'raw' / 'etcm_herbs'; raw.mkdir(parents=True)
    for i, name in enumerate(('Alpha', 'Beta', 'Gamma', 'Delta')):
        fields = [('Herb Name in Pinyin', name), ('Herb Name in Latin', 'Plantus alpha L.'),
                  ('Medicinal Part', 'Root' if name == 'Beta' else 'Leaf'),
                  ('Property', '=1+2' if i == 0 else 'Warm')]
        (raw / (name + '.json')).write_text(json.dumps({'code': 1, 'data': [{'id': 'base_information',
            'value': [{'key': k, 'value': v} for k, v in fields]}]}))
    herbs = root / 'herbs.sqlite3'; import_snapshot(root / 'raw', herbs, 'fixture-v1')
    entries = []
    for i, name in enumerate(('甲草', '乙草', '丙草', '丁草')):
        evidence = [{'block_id': 'b' + str(i), 'source': {'source_pdf_page': i + 1, 'source_document': 'fixture',
                      'source_sha256': 'a' * 64}, 'start': 0, 'end': 10}]
        fields = [{'scope': 'monograph', 'label': label, 'text': text, 'raw_text': text,
                   'overlay_ids': [], 'evidence': evidence} for label, text in (
            ('origin', '本品为植物 Plantus alpha L. 的干燥' + ('根茎' if i == 1 else '叶') + '。'),
            ('功能与主治', '测试原文' + str(i)))]
        if i == 0:
            fields[1]['scope'] = 'prepared_slices'
            fields.extend([{'scope': scope, 'label': '贮藏', 'text': text, 'raw_text': text,
                            'overlay_ids': [], 'evidence': evidence}
                           for scope, text in [('monograph', '原记录'), ('prepared_slices', '另一炮制记录')]])
        entries.append({'id': 'entry-' + str(i), 'name_zh_candidate': name, 'raw_name_zh_candidate': name,
                        'fields': fields, 'human_signoff': False, 'production_eligible': False,
                        'raw_flags': [], 'applied_overlay_ids': [], 'review_status': 'pending_human_review',
                        'title_evidence': evidence})
    view = root / 'view'; view.mkdir()
    from tcm.mineru_pharmacopoeia import digest
    (view / 'research_view.json').write_text(json.dumps({'human_signoff': False, 'production_eligible': False, 'entries': entries}))
    (view / 'revision_ledger.json').write_text('{}')
    (view / 'summary.json').write_text(json.dumps({'research_view_sha256': digest(view / 'research_view.json'), 'ledger_sha256': digest(view / 'revision_ledger.json')}))
    build(view, root / 'index')
    con = sqlite3.connect(herbs)
    records = {json.loads(r[0])['fields']['name_pinyin']['value']: json.loads(r[0]) for r in con.execute('SELECT document FROM records')}; con.close()
    enrichment = root / 'enrichment.sqlite3'
    with sqlite3.connect(enrichment) as con:
        con.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE herbs(source_record_id TEXT PRIMARY KEY,dataset_version TEXT,file_sha256 TEXT);
            CREATE TABLE name_candidates(herb_id TEXT,entry_id TEXT,name_zh TEXT,evidence TEXT);''')
        con.executemany('INSERT INTO metadata VALUES(?,?)', [('kind', '"etcm_enrichment_v1"'), ('build_complete', 'true')])
        for name, record in records.items():
            con.execute('INSERT INTO herbs VALUES(?,?,?)', (record['source_record_id'], 'fixture-v1', record['source']['file_sha256']))
        for name, i in [('Alpha', 0), ('Beta', 1), ('Gamma', 2), ('Gamma', 3)]:
            record = records[name]; candidate = {'entry_id': 'entry-' + str(i), 'name_zh': entries[i]['name_zh_candidate'],
                                                'identity_verified': False}
            con.execute('INSERT INTO name_candidates VALUES(?,?,?,?)', (record['source_record_id'], candidate['entry_id'], candidate['name_zh'], json.dumps(candidate)))
    return records, lambda: Sources(herbs, enrichment, root / 'index', 'fixture-v1')
