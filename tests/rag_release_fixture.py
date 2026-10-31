"""Synthetic raw-source fixture, with no private corpus or provider dependency."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pymupdf

from scripts.build_mineru_retrieval import build as build_index
from tcm.herbs import import_snapshot
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_revisions import fingerprint, render_view


def save(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def fixture(root):
    root = Path(root);root.mkdir(parents=True)
    pdf = root/'source.pdf'
    doc = pymupdf.open()
    for _ in range(3):doc.new_page()
    doc.save(pdf);doc.close();pdfsha=digest(pdf)
    texts=['甲草','本品为植物甲的干燥叶。','【贮藏】置干燥处。',
           '乙草','【贮藏】置通风处。','丙草','【贮藏】防潮。']
    blocks=[{'id':'b'+str(i),'raw':{'type':'text','text':t,'bbox':[100,100+i*20,500,115+i*20]},
             'source':{'source_document':'high','source_sha256':pdfsha,'source_pdf_page':1},
             'chapter_page':1,'flags':[]} for i,t in enumerate(texts)]
    def field(i,label):return {'scope':'monograph','label':label,'segments':[{'block_id':'b'+str(i),'start':0,'end':len(texts[i])}]}
    entries=[{'id':'e-alpha','name_zh_candidate':'甲草','title_block_ids':['b0'],
              'block_ids':['b0','b1','b2'],'fields':[field(1,'origin'),field(2,'贮藏')]},
             {'id':'e-beta','name_zh_candidate':'乙草','title_block_ids':['b3'],
              'block_ids':['b3','b4'],'fields':[field(4,'贮藏')]},
             {'id':'e-gamma','name_zh_candidate':'丙草','title_block_ids':['b5'],
              'block_ids':['b5','b6'],'fields':[field(6,'贮藏')]}]
    for e in entries:e.update(flags=[],review_status='pending_human_review',chapter_pages=[1])
    base=root/'base';base.mkdir();save(base/'entries.json',{'entries':entries});save(base/'blocks.json',blocks)
    entries_sha=digest(base/'entries.json');blocks_sha=digest(base/'blocks.json')
    save(base/'summary.json',{'entries_sha256':entries_sha,'blocks_sha256':blocks_sha})
    revisions=root/'revisions';revisions.mkdir()
    ledger={'entries_sha256':entries_sha,'blocks_sha256':blocks_sha,'human_signoff':False,'overlays':[]}
    view=render_view({'entries':entries},{b['id']:b for b in blocks},ledger,entries_sha,blocks_sha)
    save(revisions/'research_view.json',view);save(revisions/'revision_ledger.json',ledger)
    save(revisions/'summary.json',{'source_entries_sha256':entries_sha,'source_blocks_sha256':blocks_sha,
        'research_view_sha256':digest(revisions/'research_view.json'),'ledger_sha256':digest(revisions/'revision_ledger.json')})
    index=root/'index';build_index(revisions,index)
    rawroot=root/'raw';raw=rawroot/'etcm_herbs';raw.mkdir(parents=True)
    for name in ('Alpha','Beta','Gamma'):
        save(raw/(name+'.json'),{'code':1,'data':[{'id':'base_information','value':[
            {'key':'Herb Name in Pinyin','value':name},{'key':'Medicinal Part','value':'Leaf'}]}]})
    herbs=root/'herbs.sqlite3';import_snapshot(rawroot,herbs,'fixture-v1')
    with sqlite3.connect(herbs) as db:
        rows=db.execute('SELECT source_record_id,sha256,document FROM records ORDER BY relative_path').fetchall()
    enrichment=root/'enrichment.sqlite3'
    with sqlite3.connect(enrichment) as db:
        db.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);'
                        'CREATE TABLE herbs(source_record_id TEXT,dataset_version TEXT,file_sha256 TEXT);'
                        'CREATE TABLE name_candidates(herb_id TEXT,entry_id TEXT,name_zh TEXT);')
        meta={'kind':'etcm_enrichment_v1','build_complete':True,'dataset_version':'fixture-v1',
              'base_database_sha256':digest(herbs),'source_fingerprint':'fixture-source',
              'pharma_entries_sha256':entries_sha,'pharma_blocks_sha256':blocks_sha}
        db.executemany('INSERT INTO metadata VALUES(?,?)',[(k,json.dumps(v)) for k,v in meta.items()])
        for (sid,sha,_),e in zip(rows,entries):
            db.execute('INSERT INTO herbs VALUES(?,?,?)',(sid,'fixture-v1',sha))
            db.execute('INSERT INTO name_candidates VALUES(?,?,?)',(sid,e['id'],e['name_zh_candidate']))
    targets=root/'targets.sqlite3'
    with sqlite3.connect(targets) as db:
        db.executescript('CREATE TABLE metadata(key TEXT,value TEXT);CREATE TABLE targets(uniprot TEXT);'
                        'CREATE TABLE ingredient_rows(ordinal INTEGER);')
        meta={'kind':'etcm_target_ingredients_v1','build_complete':True,'dataset_version':'fixture-v1',
              'herb_source_fingerprint':'fixture-source','targets':0,'rows':0}
        db.executemany('INSERT INTO metadata VALUES(?,?)',[(k,json.dumps(v)) for k,v in meta.items()])
    def anchor(i,e,label):
        b=blocks[i]
        return {'kind':'pdf_block','entry_id':e,'label':label,'scope':'entry_title' if label=='entry_title' else 'monograph',
                'block_id':b['id'],'start':0,'end':len(b['raw']['text']),
                'text_sha256':hashlib.sha256(b['raw']['text'].encode()).hexdigest(),'block_sha256':fingerprint(b)}
    common={'question':'合成问题','review_status':'ai_raw_source_checked_pending_human_signoff',
            'human_signoff':False,'excluded_anchors':[],'required_facts':[],'forbidden_claims':[]}
    cases=[{**common,'case_id':'P','split':'development','expected_status':'evidence_found',
            'group_ids':['e-alpha'],'title_anchors':[anchor(0,'e-alpha','entry_title')],
            'relevant_anchors':[anchor(2,'e-alpha','贮藏')]},
           {**common,'case_id':'N','split':'development','expected_status':'missing_field',
            'group_ids':['e-alpha'],'title_anchors':[anchor(0,'e-alpha','entry_title')],'relevant_anchors':[],
            'absence_witness':{'entry_id':'e-alpha','label':'注意','start_block_id':'b0',
                'end_before_block_id':'b3','interval_sha256':fingerprint(blocks[:3])}},
           {**common,'case_id':'A','split':'reserved','expected_status':'clarification_required',
            'group_ids':['e-beta','e-gamma'],'title_anchors':[anchor(3,'e-beta','entry_title'),anchor(5,'e-gamma','entry_title')],
            'relevant_anchors':[anchor(4,'e-beta','贮藏'),anchor(6,'e-gamma','贮藏')]}]
    seeds=root/'seeds.json';save(seeds,{'source_blocks_sha256':blocks_sha,'cases':cases,
        'expected_counts':{'evidence_found':1,'missing_field':1,'clarification_required':1},
        'expected_splits':{'development':2,'reserved':1}})
    return {k:str(v) for k,v in dict(base=base,revisions=revisions,index=index,herbs=herbs,
        enrichment=enrichment,targets=targets,source_root=rawroot,pdf_high=pdf,pdf_full=pdf,seeds=seeds).items()}
