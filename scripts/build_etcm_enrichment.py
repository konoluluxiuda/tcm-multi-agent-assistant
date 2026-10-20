"""Build an immutable derived Chinese display/association catalog; source files remain read-only."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import unicodedata

from tcm.etcm_localization import dictionary_translation,VERSION

KINDS={'ingredient':'Ingredient Name','target':'Target Name','disease':'Disease Name',
       'chinese_patent_drug':'Formula Name in Chinese Pinyin',
       'traditional_chinese_medicine_formula':'Formula Name in Chinese Pinyin'}
DIRECTORIES={'ingredient':'etcm_ingredients','target':'etcm_targets','disease':'etcm_diseases'}


def dumps(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def key(value):return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())
def stable(prefix,value):return prefix+':'+hashlib.sha256(value.encode()).hexdigest()
def values(value):return value if isinstance(value,list) else [value] if isinstance(value,str) else []


def pharma_candidates(entries,blocks):
    """Exact normalized title pinyin yields lexical candidates, never entity equivalence."""
    result=defaultdict(list)
    if not entries or not blocks:return result
    block_data=json.loads(Path(blocks).read_text());block_data=block_data.get('blocks',[]) if isinstance(block_data,dict) else block_data
    index={b['id']:b for b in block_data}
    for entry in json.loads(Path(entries).read_text())['entries']:
        if entry.get('title_flags'):continue
        name=entry.get('name_zh_candidate')
        if not isinstance(name,str) or not re.fullmatch(r'[\u4e00-\u9fff]{2,20}',name):continue
        for identifier in entry.get('title_block_ids',[]):
            block=index[identifier];text=block.get('text',block.get('raw',{}).get('text','')).strip()
            prefix=re.split(r'\s+(?=[A-Z]{3,})',text,maxsplit=1)[0]
            if re.fullmatch(r'[A-Za-z -]{2,70}',prefix):
                normalized=re.sub(r'[ -]','',prefix).casefold()
                result[normalized].append({'name_zh':name,'entry_id':entry['id'],
                    'method':'normalized_pinyin_title_candidate','identity_verified':False,
                    'block_id':identifier,'title_text':text,'source':block.get('source')})
    return result


def initialize(con):
    con.executescript('''
      PRAGMA journal_mode=WAL;
      CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE herbs(source_record_id TEXT PRIMARY KEY,dataset_version TEXT NOT NULL,file_sha256 TEXT NOT NULL,document TEXT NOT NULL);
      CREATE TABLE translations(herb_id TEXT NOT NULL,field TEXT NOT NULL,translation TEXT NOT NULL,PRIMARY KEY(herb_id,field));
      CREATE TABLE translation_jobs(field TEXT NOT NULL,value TEXT NOT NULL,translation TEXT,PRIMARY KEY(field,value));
      CREATE TABLE nodes(node_id TEXT PRIMARY KEY,kind TEXT NOT NULL,name TEXT NOT NULL,identifier TEXT,identity_status TEXT NOT NULL);
      CREATE TABLE edges(edge_id TEXT PRIMARY KEY,herb_id TEXT NOT NULL,node_id TEXT NOT NULL,kind TEXT NOT NULL,
                         source_pointer TEXT NOT NULL,raw_row TEXT NOT NULL,capture_status TEXT NOT NULL);
      CREATE TABLE file_candidates(node_id TEXT NOT NULL,relative_path TEXT NOT NULL,status TEXT NOT NULL,PRIMARY KEY(node_id,relative_path));
      CREATE TABLE name_candidates(herb_id TEXT NOT NULL,name_zh TEXT NOT NULL,entry_id TEXT NOT NULL,evidence TEXT NOT NULL,
                                   PRIMARY KEY(herb_id,name_zh,entry_id));
      CREATE INDEX edges_by_herb ON edges(herb_id,kind,source_pointer);
      CREATE INDEX edges_by_node ON edges(node_id,herb_id);
      CREATE INDEX chinese_name_lookup ON name_candidates(name_zh,herb_id);
    ''')


def add_edge(con,herb_id,kind,row,pointer,capture):
    labels=[s for s in values(row.get(KINDS.get(kind,''))) if isinstance(s,str) and s.strip()]
    if not labels:return False
    label=labels[0];identifier=None;status='name_group_not_entity_verified'
    if kind=='target':
        identifiers=values(row.get('UniProt Accession Number'))
        if len(identifiers)==1 and isinstance(identifiers[0],str) and re.fullmatch(r'[A-Z0-9]{6,10}',identifiers[0]):
            identifier=identifiers[0];status='source_declared_uniprot_not_externally_verified'
    if kind in ('reference_pmid','external_reference'):
        identifier=label;status='source_declared_identifier'
    node_id=stable(kind,identifier if identifier else key(label))
    con.execute('INSERT OR IGNORE INTO nodes VALUES (?,?,?,?,?)',(node_id,kind,label,identifier,status))
    edge_id=stable('source-row',herb_id+'\0'+pointer)
    con.execute('INSERT INTO edges VALUES (?,?,?,?,?,?,?)',(edge_id,herb_id,node_id,kind,pointer,dumps(row),dumps(capture)))
    return True


def finalize_file_candidates(con,catalog):
    for row in con.execute('SELECT node_id,kind,name FROM nodes'):
        for relative in catalog.get((row[1],key(row[2].replace('_',' '))),[]):
            con.execute('INSERT OR IGNORE INTO file_candidates VALUES (?,?,?)',(row[0],relative,'filename_match_only_internal_identity_unverified'))
    # One identifier may have several source labels; retain all filename candidates,
    # and flag the label disagreement instead of silently choosing the first name.
    labels=defaultdict(set)
    for node_id,name in con.execute("SELECT DISTINCT node_id,json_extract(raw_row,'$.\"Target Name\"[0]') FROM edges WHERE kind='target'"):
        if not isinstance(name,str):continue
        labels[node_id].add(key(name))
        for relative in catalog.get(('target',key(name.replace('_',' '))),[]):
            con.execute('INSERT OR IGNORE INTO file_candidates VALUES (?,?,?)',(node_id,relative,'filename_match_only_internal_identity_unverified'))
    for node_id,names in labels.items():
        if len(names)>1:
            con.execute("UPDATE nodes SET identity_status=identity_status||'_multiple_source_labels' WHERE node_id=? AND identity_status NOT LIKE '%multiple_source_labels'",(node_id,))


def build(source,database,version,out,entries=None,blocks=None):
    source=Path(source).resolve();out=Path(out).resolve();database=Path(database).resolve()
    if out.is_relative_to(source):raise ValueError('Output must be outside original data')
    if out.exists():raise FileExistsError('Use a fresh output directory')
    base_hash=digest(database)
    base=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)
    records=base.execute("SELECT source_record_id,relative_path,sha256,document FROM records WHERE dataset_version=? AND state='valid' ORDER BY relative_path",(version,)).fetchall()
    base.close()
    if not records:raise ValueError('Selected snapshot has no records')
    names=pharma_candidates(entries,blocks)
    catalog=defaultdict(list)
    for kind,directory in DIRECTORIES.items():
        for path in sorted((source/directory).glob('*.json')):
            if path.resolve().is_relative_to(source):catalog[(kind,key(path.stem.replace('_',' ')))].append(str(path.relative_to(source)))
    out.mkdir(parents=True,mode=0o700)
    con=sqlite3.connect(out/'enrichment.sqlite3');initialize(con)
    stats=Counter();fingerprints=[]
    meta={'kind':'etcm_enrichment_v1','build_complete':False,'dataset_version':version,
          'base_database_sha256':base_hash,'localization_version':VERSION,
          'translation_stage':'dictionary_and_pending_machine','relations_are_source_assertions':True,
          'pharma_entries_sha256':digest(entries) if entries else None,'pharma_blocks_sha256':digest(blocks) if blocks else None}
    for k,v in meta.items():con.execute('INSERT INTO metadata VALUES (?,?)',(k,dumps(v)))
    try:
        for number,(herb_id,relative,expected,document) in enumerate(records,1):
            path=(source/relative).resolve()
            if not path.is_relative_to(source):raise ValueError('Source path escaped input root')
            raw=path.read_bytes();actual=hashlib.sha256(raw).hexdigest()
            if actual!=expected:raise ValueError('Original source differs from imported snapshot: '+relative)
            original=json.loads(raw);doc=json.loads(document);fingerprints.append([relative,actual])
            con.execute('INSERT INTO herbs VALUES (?,?,?,?)',(herb_id,version,actual,document))
            for field,data in doc['fields'].items():
                if data['state']!='present':continue
                value=data['value'];translated=dictionary_translation(field,value)
                if translated is None:
                    if isinstance(value,str) and field not in ('name_pinyin','latin_name','english_name'):
                        translated={'text':None,'status':'pending_local_translation','human_reviewed':False}
                        con.execute('INSERT OR IGNORE INTO translation_jobs VALUES (?,?,NULL)',(field,value))
                    else:translated={'text':value,'status':'preserved_identifier','human_reviewed':False}
                con.execute('INSERT INTO translations VALUES (?,?,?)',(herb_id,field,dumps(translated)))
                stats['translation_'+translated['status']]+=1
            pinyin=re.sub(r'[ -]','',doc['fields']['name_pinyin']['value']).casefold()
            for candidate in names.get(pinyin,[]):
                con.execute('INSERT OR IGNORE INTO name_candidates VALUES (?,?,?,?)',
                            (herb_id,candidate['name_zh'],candidate['entry_id'],dumps(candidate)))
            for si,section in enumerate(original.get('data',[])):
                if not isinstance(section,dict) or section.get('id')!='related_table':continue
                for ti,table in enumerate(section.get('value',[])):
                    kind=table.get('type')
                    if kind not in KINDS:stats['unknown_relation_tables']+=1;continue
                    capture=[s for s in original.get('complete_fetch_summary',[]) if isinstance(s,dict) and s.get('section')=='related_table' and s.get('type')==kind]
                    for ri,row in enumerate(table.get('value',[])):
                        pointer=f'/data/{si}/value/{ti}/value/{ri}'
                        if isinstance(row,dict) and add_edge(con,herb_id,kind,row,pointer,capture):stats['edges_'+kind]+=1
                        else:stats['unresolved_relation_rows']+=1
            # Explicit identifiers in basic fields are links, not inferred evidence for each claim.
            for field,kind in [('reference_pmids','reference_pmid'),('cross_references','external_reference')]:
                data=doc['fields'][field]
                if data['state']!='present':continue
                items=data['value'];raw_item=data['source'].get('raw_item',{})
                params=raw_item.get('linkformat',{}).get('value',{}).get('param',[])
                if kind=='external_reference' and (not isinstance(params,list) or len(params)!=len(items)):
                    stats['unpaired_cross_reference_records']+=1;continue
                for i,item in enumerate(items):
                    label=item if kind=='reference_pmid' else params[i]
                    if kind=='external_reference' and (not isinstance(label,str) or not re.match(r'^https?://',label)):
                        stats['invalid_external_links']+=1;continue
                    row={'label':item,'identifier':label}
                    # Reuse the edge builder with an explicit label field for identifier links.
                    node_id=stable(kind,label)
                    con.execute('INSERT OR IGNORE INTO nodes VALUES (?,?,?,?,?)',(node_id,kind,item,label,'source_declared_identifier'))
                    pointer=data['source']['json_pointer']+'/'+str(i)
                    con.execute('INSERT INTO edges VALUES (?,?,?,?,?,?,?)',
                                (stable('source-row',herb_id+'\0'+pointer),herb_id,node_id,kind,pointer,dumps(row),'[]'))
                    stats['edges_'+kind]+=1
            if number%100==0:
                con.commit();print(dumps({'herbs_processed':number,'edges':sum(v for k,v in stats.items() if k.startswith('edges_'))}),flush=True)
        finalize_file_candidates(con,catalog)
        if digest(database)!=base_hash:raise ValueError('ETCM database changed during build')
        stats.update({'herbs':len(records),'nodes':con.execute('SELECT COUNT(*) FROM nodes').fetchone()[0],
                      'edges':con.execute('SELECT COUNT(*) FROM edges').fetchone()[0],
                      'name_candidates':con.execute('SELECT COUNT(*) FROM name_candidates').fetchone()[0],
                      'herbs_with_chinese_candidates':con.execute('SELECT COUNT(DISTINCT herb_id) FROM name_candidates').fetchone()[0],
                      'file_candidates':con.execute('SELECT COUNT(*) FROM file_candidates').fetchone()[0]})
        meta.update(build_complete=True,source_fingerprint=hashlib.sha256(dumps(fingerprints).encode()).hexdigest(),stats=dict(stats))
        for k,v in meta.items():con.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(k,dumps(v)))
        con.commit();con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:con.close()
    (out/'source_files.json').write_text(dumps(fingerprints)+'\n')
    (out/'receipt.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    print(dumps({'status':'built','stats':dict(stats)}),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--database',type=Path,default=Path('var/tcm/herbs.sqlite3'))
    parser.add_argument('--version',default='local-2026-10-10')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--pharma-entries',type=Path)
    parser.add_argument('--pharma-blocks',type=Path)
    args=parser.parse_args()
    if bool(args.pharma_entries)!=bool(args.pharma_blocks):parser.error('Both pharmacopoeia files required')
    build(args.source,args.database,args.version,args.out,args.pharma_entries,args.pharma_blocks)


if __name__=='__main__':main()
