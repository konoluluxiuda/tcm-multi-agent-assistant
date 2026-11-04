"""Build DEVELOPMENT references from registered read-only sources, never a model.

This verifies source consistency, not botanical identity, OCR quality or heldout
independence. Ambiguous/missing references are listed, not relabelled successes.
"""
import argparse
import json
from pathlib import Path
from scripts.eval_requirements_generalization import _read_suite,_save
from tcm.dataops.research_sources import ReleaseSources
from tcm.dataops.contracts import Query
from tcm.evaluation.api_runner import _evidence_sources
from tcm.evaluation.requirements_benchmark import Scenario


def build(suite,release,out):
    text=Path(suite).read_text()
    raw=json.loads(text) if text.lstrip().startswith('[') else [json.loads(x) for x in text.splitlines() if x.strip()]
    # Old draft witnesses were incomplete; rebuild them from sources, not guessed defaults.
    for item in raw:item['expected']['source_assertions']=[]
    items=[Scenario.model_validate(x) for x in raw];out=Path(out)
    out.mkdir(parents=True,exist_ok=False,mode=0o700)
    cases=[];issues=[];metadata=[]
    with ReleaseSources(release) as src:
        for scenario in items:
            raw=scenario.model_dump();raw['split']='dev';e=raw['expected'];e['source_assertions']=[];e['missing_assertions']=[]
            e['object_aliases']={};records={}
            for i,name in enumerate(e['objects']):
                selected=src.query(Query(names=[name],limit=20))
                if len(selected['records'])!=1:
                    issues.append({'id':scenario.id,'object':name,'reason':'non_unique_source','count':len(selected['records'])});continue
                rec=selected['records'][0];records[f'o{i+1}']=rec;records[name]=rec
                aliases=[rec['fields']['name_pinyin']['value']]
                e['object_aliases'][name]=[n for n in aliases if n!=name]
            for req in e['requirements']:
                ns,name=req['namespace'],req['name'];ref=req['object_ref']
                if ns=='dataset':
                    e['source_assertions'].append({'namespace':'dataset','name':name,'source_document':'runtime_catalog','value_contains':'ETCM'})
                    continue
                if name in ('ingredient','target','common_target'):
                    req.update(kind='relation',object_refs=[ref] if ref else req['object_refs'],object_ref=None)
                    from tcm.etcm_enrichment import relations
                    for object_ref in req['object_refs']:
                        rec=records.get(object_ref)
                        if not rec:continue
                        if name=='common_target':
                            from tcm.relation_queries import shared_targets
                            group=[records.get(x) for x in req['object_refs']]
                            if len(group)!=2 or any(x is None for x in group):
                                issues.append({'id':scenario.id,'reason':'common_target_needs_two_bound_sources'});continue
                            data=shared_targets(src.paths[1],group,src.version)
                        else:data=relations(src.paths[1],rec,src.version,kind=name,limit=20)
                        witness=data['items'][0] if data['items'] else None
                        e['source_assertions'].append({'object_ref':object_ref,'namespace':ns,'name':name,
                            'source_document':rec['source']['relative_file_path'],'source_sha256':rec['source']['file_sha256'],
                            'total':data['total'],'returned_count':data['returned_count'],
                            'value_contains':str(witness.get('identifier',witness.get('name'))) if witness else '[]'})
                    continue
                rec=records.get(ref)
                if not rec:continue
                if ns=='etcm':
                    cell=rec['fields'].get(name,{})
                    if cell.get('value') is None:
                        e['missing_assertions'].append(req);continue
                    anchors=_evidence_sources(cell['value'],ref,ns,name,cell.get('source',rec['source']))
                else:
                    inspected=src.inspect(rec,[name]);cell=inspected['fields'].get(name,{})
                    if cell.get('status')=='missing':e['missing_assertions'].append(req);continue
                    if cell.get('value') is None:
                        issues.append({'id':scenario.id,'object':ref,'field':name,'reason':cell.get('status',inspected['status'])});continue
                    anchors=[]
                    for evidence in cell.get('evidence',[]):anchors.extend(_evidence_sources(cell['value'],ref,ns,name,evidence))
                if not anchors:
                    issues.append({'id':scenario.id,'object':ref,'field':name,'reason':'no_source_anchor'});continue
                anchor=anchors[0]
                e['source_assertions'].append({k:v for k,v in {**anchor,'value_contains':str(anchor['value'])}.items() if k!='value' and v is not None})
            metadata.append({'id':scenario.id,'source_revision':src.revision,'source_identity_verified':False,'reference_review':'programmatic_source_consistency_only'})
            cases.append(Scenario.model_validate(raw).model_dump())
        src.assert_unchanged()
    _save(out/'questions.private.json',cases)
    report={'status':'needs_review' if issues else 'source_references_prepared','scenarios':len(cases),
        'turns':sum(len(s['turns']) for s in cases),'issues':issues,'metadata':metadata,'real_model_requests':0,
        'independent_heldout':False,'human_identity_reviewed':False}
    _save(out/'reference_report.private.json',report);return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--suite',type=Path,required=True);p.add_argument('--release',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();r=build(a.suite,a.release,a.out);print(json.dumps({k:v for k,v in r.items() if k!='metadata'},ensure_ascii=False))
