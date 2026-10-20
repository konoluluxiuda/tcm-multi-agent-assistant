"""Translate pending display fields locally and export bilingual herbs; never edit source JSON."""
import argparse
from collections import defaultdict
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import sqlite3
from unittest.mock import patch

from tcm.local_translation import LocalTranslator


def run(database,model,export=None):
    if export and Path(export).exists():raise FileExistsError('Use a fresh bilingual export path')
    con=sqlite3.connect(database)
    meta={k:json.loads(v) for k,v in con.execute('SELECT key,value FROM metadata')}
    if meta.get('kind')!='etcm_enrichment_v1' or not meta.get('build_complete'):raise ValueError('Build the source catalog first')
    targets=defaultdict(list)
    for identifier,document in con.execute('SELECT source_record_id,document FROM herbs'):
        for field,data in json.loads(document)['fields'].items():
            if data['state']=='present' and isinstance(data['value'],str):targets[(field,data['value'])].append(identifier)
    pending=con.execute('SELECT field,value FROM translation_jobs WHERE translation IS NULL ORDER BY field,value').fetchall()
    attempts=[]
    def deny(*args,**kwargs):attempts.append('blocked');raise RuntimeError('Network forbidden during source translation')
    try:
        with ExitStack() as stack:
            for obj,name in [(socket.socket,'connect'),(socket.socket,'connect_ex'),(socket.socket,'sendto'),(socket,'getaddrinfo')]:
                stack.enter_context(patch.object(obj,name,deny))
            translator=LocalTranslator(model)
            for start in range(0,len(pending),32):
                jobs=pending[start:start+32]
                results=translator.translate([value for field,value in jobs])
                for (field,value),result in zip(jobs,results):
                    result['source_value']=value
                    encoded=json.dumps(result,ensure_ascii=False)
                    con.execute('UPDATE translation_jobs SET translation=? WHERE field=? AND value=?',(encoded,field,value))
                    con.executemany('UPDATE translations SET translation=? WHERE herb_id=? AND field=?',
                                    [(encoded,identifier,field) for identifier in targets[(field,value)]])
                con.commit()
                print(json.dumps({'translated_unique_values':min(start+32,len(pending)),'total_pending_at_start':len(pending)}),flush=True)
        if attempts:raise RuntimeError('Unexpected network attempt recorded')
        for key,value in {'translation_stage':'local_machine_candidates_complete','model_receipt':translator.receipt,
                          'translation_network_attempts':0}.items():
            con.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(key,json.dumps(value)))
        con.commit();con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        if export:
            path=Path(export);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as output:
                for identifier,document in con.execute('SELECT source_record_id,document FROM herbs ORDER BY source_record_id'):
                    doc=json.loads(document)
                    doc['name_zh_candidates']=[json.loads(r[0]) for r in con.execute('SELECT evidence FROM name_candidates WHERE herb_id=?',(identifier,))]
                    for field,translation in con.execute('SELECT field,translation FROM translations WHERE herb_id=?',(identifier,)):
                        doc['fields'][field]['translation']=json.loads(translation)
                    doc['relation_counts']={kind:count for kind,count in con.execute('SELECT kind,COUNT(*) FROM edges WHERE herb_id=? GROUP BY kind',(identifier,))}
                    output.write(json.dumps(doc,ensure_ascii=False)+'\n')
        summary={'status':'completed','unique_values_translated_this_run':len(pending),
                 'pending_remaining':con.execute('SELECT COUNT(*) FROM translation_jobs WHERE translation IS NULL').fetchone()[0],
                 'model_calls_external':0,'network_attempts':0,'human_reviewed':False}
        Path(database).with_name('translation_report.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(json.dumps(summary),flush=True)
    finally:con.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--export',type=Path)
    args=parser.parse_args();run(args.database,args.model,args.export)


if __name__=='__main__':main()
