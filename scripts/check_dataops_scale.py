"""Freeze raw-source references and verify 50-record local DataOps v1/v2 delivery."""
import argparse
import json
from pathlib import Path
import time

from tcm.dataops.contracts import Goal, Query, LABELS
from tcm.dataops.data_access import Sources, fingerprint
from tcm.dataops.evaluation import MeasuredDriver, Probe, write
from tcm.dataops.graph import Engine
from tcm.dataops.workspace import Workspaces

ROOT=Path(__file__).resolve().parents[1]


def check(args):
    if args.out.exists():raise FileExistsError('Use a fresh output directory')
    args.out.mkdir(parents=True)
    factory=lambda:Sources(ROOT/'var/tcm/herbs.sqlite3',
        ROOT/'var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3',ROOT/'var/mineru_retrieval_v2')
    raw_root=ROOT.parent/'HDCTI/dataset/ETCM2.0'
    view_path=ROOT/'var/mineru_revisions_v2/research_view.json'
    entries={e['id']:e for e in json.loads(view_path.read_text())['entries']}
    manifest=json.loads((ROOT/'var/mineru_retrieval_v2/manifest.json').read_text())
    assert fingerprint(view_path)==manifest['view_sha256']
    reference={}
    with factory() as src:
        # Select the range independently of Sources.query, stable IDs as in the public contract.
        docs=[json.loads(r[0]) for r in src.h.execute("SELECT document FROM records WHERE state='valid' AND dataset_version=? ORDER BY source_record_id LIMIT 50",(src.version,))]
        assert len(docs)==50
        for record in docs:
            sid=record['source_record_id'];source=record['source']
            path=(raw_root/source['relative_file_path']).resolve()
            assert path.is_relative_to(raw_root.resolve()) and fingerprint(path)==source['file_sha256']
            data=json.loads(path.read_text());items=next(s['value'] for s in data['data'] if s['id']=='base_information')
            raw={i['key']:i['value'] for i in items}
            candidates=[r[0] for r in src.e.execute('SELECT DISTINCT entry_id FROM name_candidates WHERE herb_id=? ORDER BY entry_id',(sid,))]
            # Structured catalog contract trims surrounding whitespace, preserving raw provenance.
            expected={'medicinal_part':(raw.get('Medicinal Part') or '').strip() or None,'latin_name':None}
            from tcm.herbs import text_value
            latin=raw.get('Herb Name in Latin')
            expected['latin_name']=text_value(latin) or None if latin else None
            for field in ('功能与主治','注意','贮藏'):
                parts=[f['raw_text'] for f in entries[candidates[0]]['fields'] if f['scope'] in ('monograph','prepared_slices')
                       and f['label']==LABELS[field] and f['raw_text'].strip()] if len(candidates)==1 else []
                expected[field]=parts[0] if len(parts)==1 else None
            reference[sid]=expected
        revision=src.revision
    goal=Goal(query=Query(source_ids=sorted(reference),limit=50))
    write(args.out/'protocol.json',{'kind':'dataops-real-50-local-scale-v1','source_revision':revision,
        'goal':goal.model_dump(),'code':{str(p.relative_to(ROOT)):fingerprint(p) for p in (ROOT/'tcm/dataops').glob('*.py')},
        'reference_frozen_before_execution':True,'driver':'synthetic_observation_policy','external_model_calls':0,
        'review_status':'source_reconciled_pending_human_signoff'})
    write(args.out/'reference.json',reference)
    store=Workspaces(args.out/'work');probe=Probe(factory);stats=[];parent=None;parent_hash=None
    for version in (1,2):
        current=goal if version==1 else goal.model_copy(update={'document_fields':['功能与主治','注意','贮藏']})
        extra={} if parent is None else {'parent_id':parent['id'],'expected_revision':1}
        doc,_=store.reserve_agent('scale-owner','scale-v'+str(version),current.model_dump(),revision,
            question='整理50条来源并保留出处' if version==1 else '为50条结果增加贮藏',**extra)
        driver=MeasuredDriver('A',current);started=time.perf_counter();cut=len(probe.reads)
        result=Engine(store,'scale-owner',doc['id'],probe,driver).run()
        write(args.out/('v'+str(version)+'-result.json'),result)
        if result['status']!='complete':
            write(args.out/'failure.json',{'status':'failed','version':version,'progress':result.get('progress')})
            raise AssertionError('Scale task stopped: '+str(result.get('error')))
        assert {r['source_id'] for r in result['result']['rows']}==set(reference)
        for row in result['result']['rows']:
            for field,cell in row['cells'].items():assert cell['value']==reference[row['source_id']][field],(row['source_id'],field)
        file,_=store.artifact('scale-owner',doc['id']);reads=probe.reads[cut:]
        if version==1:
            parent=result;parent_hash=fingerprint(file)
        else:
            original,_=store.artifact('scale-owner',parent['id']);assert fingerprint(original)==parent_hash
            assert all(r['fields']==['贮藏'] for r in reads if r['kind']=='inspect')
            assert len(result['result']['diff'])==50
        stats.append({'version':version,'records':50,'source_field_checks':50*len(result['result']['rows'][0]['cells']),
            'elapsed_seconds':time.perf_counter()-started,'context_max_bytes':max(driver.context_bytes),
            'policy_steps':len(driver.context_bytes),'leaf_reads':result['progress']['leaf_reads'],
            'reused_cells':result['progress']['reused_cells'],'artifact_bytes':result['artifact']['bytes'],
            'verification':result['artifact']['verification'],'external_model_calls':0})
    with factory() as src:assert src.revision==revision
    summary={'status':'passed','records':50,'versions':stats,'v1_preserved':True,'sources_unchanged':True,
        'external_model_calls':0,'real_model_performance':'not_run','cost':None,
        'interpretation':'Single local warm-cache run, not a production latency percentile or LLM accuracy score.'}
    write(args.out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    check(p.parse_args())
