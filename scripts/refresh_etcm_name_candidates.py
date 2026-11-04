"""Publish an additive name-candidate snapshot without modifying a registered DB."""
import argparse
import json
from pathlib import Path
import re
import sqlite3

from scripts.build_etcm_enrichment import pharma_candidates,digest,dumps
from tcm.etcm_name_candidates import VERSION


def build(database,entries,blocks,output):
    database,entries,blocks,output=map(lambda p:Path(p).resolve(),(database,entries,blocks,output))
    if output.exists():raise FileExistsError('Never overwrite a name snapshot')
    if output in (database,entries,blocks) or output.is_relative_to(database.parent):
        raise ValueError('Publish outside the registered input directory')
    before={str(p):digest(p) for p in (database,entries,blocks)}
    names=pharma_candidates(entries,blocks)
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    destination=output/'enrichment.sqlite3'
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as source:
        meta={k:json.loads(v) for k,v in source.execute('SELECT key,value FROM metadata')}
        if meta.get('kind')!='etcm_enrichment_v1' or meta.get('build_complete') is not True:
            raise ValueError('Input enrichment is incomplete')
        with sqlite3.connect(destination) as db:
            source.backup(db)
            old_count=db.execute('SELECT COUNT(*) FROM name_candidates').fetchone()[0]
            additions=[]
            for sid,version,sha,document in db.execute('SELECT * FROM herbs ORDER BY source_record_id').fetchall():
                record=json.loads(document)
                if record['source_record_id']!=sid or record['source']['file_sha256']!=sha:
                    raise ValueError('Source binding differs')
                pinyin=re.sub(r'[ -]','',record['fields']['name_pinyin']['value']).casefold()
                for candidate in names.get(pinyin,[]):
                    proof={**candidate,'herb_source':record['source'],'matched_pinyin':record['fields']['name_pinyin']['value'],
                           'candidate_input_sha256':{'entries':before[str(entries)],'blocks':before[str(blocks)]}}
                    changed=db.execute('INSERT OR IGNORE INTO name_candidates VALUES(?,?,?,?)',
                        (sid,candidate['name_zh'],candidate['entry_id'],dumps(proof))).rowcount
                    if changed:additions.append({'source_record_id':sid,'name_zh':candidate['name_zh'],
                                                 'entry_id':candidate['entry_id'],'evidence':proof})
            receipt={'kind':'additive_name_candidate_snapshot_v1','parser_version':VERSION,
                     'input_sha256':before,'old_candidate_rows':old_count,'added_rows':len(additions),
                     'candidate_rows':old_count+len(additions),'identity_verified':False,
                     'source_rows_unchanged':True,'additions':additions}
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('name_candidate_refresh',dumps({k:v for k,v in receipt.items() if k!='additions'})))
            db.commit();db.execute('PRAGMA wal_checkpoint(TRUNCATE)');db.execute('PRAGMA journal_mode=DELETE')
    if any(digest(p)!=sha for p,sha in before.items()):raise ValueError('Source changed during snapshot build')
    receipt['output_sha256']=digest(destination)
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    destination.chmod(0o600)
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('database','entries','blocks','output'):p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args();r=build(args.database,args.entries,args.blocks,args.output)
    print(json.dumps({k:r[k] for k in ('old_candidate_rows','added_rows','candidate_rows','identity_verified')}))
