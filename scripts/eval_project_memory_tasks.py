"""Synthetic transfer diagnostics: retrieval and context application are separate metrics."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch
from scripts.eval_project_semantic import evaluate
from tcm.project_memory import ProjectStore,ProjectConflict
from tcm.project_semantic import apply_context,memory_reference


def applications(dataset):
    results=[];names=sorted({h for p in dataset['projects'] for h in p['herbs']})
    for case in dataset['application_cases']:
        with tempfile.TemporaryDirectory() as tmp:
            store=ProjectStore(Path(tmp)/'projects.db')
            project=next(p for p in dataset['projects'] if p['id']==case['project'])
            saved=store.save('user',{k:v for k,v in project.items() if k!='id'})
            if case.get('mutation')=='delete':store.delete('user',saved['id'],1)
            elif case.get('mutation')=='update':store.save('user',{**{k:v for k,v in project.items() if k!='id'},'question':'注意'},identifier=saved['id'],revision=1)
            try:
                value=apply_context(store,case.get('owner','user'),saved['id'],1,case['query'],names=names)
                rejected=False
            except (ProjectConflict,KeyError):value=None;rejected=True
            correct=rejected if case.get('reject') else not rejected and value['herbs']==case['herbs'] and value['question']==case['question']
            results.append({'id':case['id'],'query':case['query'],'passed':correct,'rejected':rejected,
                            'expected':{k:case[k] for k in ('herbs','question','reject') if k in case},
                            'actual':{k:value[k] for k in ('herbs','question','question_source')} if value else None})
    return results


def run(source,out,retrieval_from=None):
    out.mkdir(parents=True,exist_ok=False);dataset=json.loads(source.read_text());fingerprint=hashlib.sha256(source.read_bytes()).hexdigest()
    if retrieval_from:
        retrieval=json.loads(retrieval_from.read_text())
        if retrieval['dataset_sha256']!=fingerprint:raise ValueError('Different frozen dataset')
        (out/'retrieval.json').write_text(json.dumps(retrieval,ensure_ascii=False,indent=2)+'\n')
    else:
        # Stable project IDs make lexical/ranking ties reproducible across runs.
        identities=iter(uuid.uuid5(uuid.NAMESPACE_URL,'tcm-transfer:'+p['id']) for p in dataset['projects'])
        with patch('tcm.project_memory.uuid.uuid4',side_effect=lambda:next(identities)):
            evaluate(source,out/'retrieval.json')
        retrieval=json.loads((out/'retrieval.json').read_text())
    cases=applications(dataset)
    (out/'application.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
    routing=[{'query':c['query'],'memory_route_triggered':memory_reference(c['query']),'has_relevant_project':bool(c['relevant'])} for c in dataset['cases']]
    report={'status':'completed','dataset_sha256':fingerprint,'dataset_kind':dataset['kind'],
            'retrieval_metrics':retrieval['metrics'],'application':{'total':len(cases),'passed':sum(c['passed'] for c in cases),'failed_ids':[c['id'] for c in cases if not c['passed']]},
            'routing':routing,'external_model_calls':0,'retrieval_replayed':bool(retrieval_from),
            'limitation':'Known-project application is evaluated separately; no automatic top-1 selection and no clinical or end-to-end success claim.'}
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report['application']))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('eval/project_memory_tasks_v2.json'));p.add_argument('--out',type=Path,required=True);p.add_argument('--retrieval-from',type=Path);a=p.parse_args();run(a.source,a.out,a.retrieval_from)
