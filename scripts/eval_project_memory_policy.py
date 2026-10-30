"""Replay frozen development scores to evaluate routing/scope policy, not new model quality."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from scripts.eval_project_memory_tasks import applications
from tcm.project_memory import ProjectStore
from tcm.project_semantic import memory_reference, recall


def run(source, baseline, out):
    dataset=json.loads(source.read_text());old=json.loads(baseline.read_text())
    fingerprint=hashlib.sha256(source.read_bytes()).hexdigest()
    if old['dataset_sha256']!=fingerprint:raise ValueError('Frozen dataset mismatch')
    rows=[]
    with tempfile.TemporaryDirectory() as tmp:
        store=ProjectStore(Path(tmp)/'projects.db');labels={}
        for project in dataset['projects']:
            with patch('tcm.project_memory.uuid.uuid4',return_value=uuid.uuid5(uuid.NAMESPACE_URL,'tcm-transfer:'+project['id'])):
                doc=store.save('evaluation',{k:v for k,v in project.items() if k!='id'})
            labels[doc['id']]=project['id']
        # Includes a known catalog herb for which no project was saved.
        names=sorted({h for p in dataset['projects'] for h in p['herbs']}|{'黄芪'})
        for case,previous in zip(dataset['cases'],old['cases'],strict=True):
            assert case['query']==previous['query']
            calls=[]
            def ranker(query,docs):
                calls.append(query)
                return {d['id']:previous['cosine'][labels[d['id']]] for d in docs}
            result=recall(store,'evaluation',case['query'],names=names,ranker=ranker)
            actual=[labels[d['id']] for d in result['items']]
            rows.append({'query':case['query'],'relevant':case['relevant'],'before':previous['results']['hybrid'],
                         'after':actual,'route':memory_reference(case['query']),
                         'method':result.get('method'),'ranker_used':bool(calls),
                         'candidate_set_exact':set(actual)==set(case['relevant'])})
    positive=[r for r in rows if r['relevant']];negative=[r for r in rows if not r['relevant']]
    applied=applications(dataset)
    report={'dataset_sha256':fingerprint,'baseline_sha256':hashlib.sha256(baseline.read_bytes()).hexdigest(),
            'kind':'known_development_score_replay_not_independent_evaluation',
            'external_model_calls':0,'new_embedding_calls':0,
            'positive_routes':sum(r['route'] for r in positive),'positive_cases':len(positive),
            'negative_no_candidate':sum(not r['after'] for r in negative),'negative_cases':len(negative),
            'exact_candidate_sets':sum(r['candidate_set_exact'] for r in rows),
            'application_passed':sum(r['passed'] for r in applied),'application_total':len(applied),
            'cases':rows,'application':applied,
            'limitation':'Replayed rounded scores from full original questions; current ranking inputs may differ. Listing coverage is not Recall@3. Verify live local encoding separately.'}
    out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('x') as stream:stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','application')},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('eval/project_memory_tasks_v2.json'));p.add_argument('--baseline',type=Path,default=Path('audits/project_memory_tasks_v2/retrieval.json'));p.add_argument('--out',type=Path,required=True);a=p.parse_args();run(a.source,a.baseline,a.out)
