"""Fixed development comparison: lexical, local dense and hybrid project recall."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time

from tcm.project_memory import ProjectStore
from tcm.project_semantic import local_rank, lexical_score, document_text, recall


def evaluate(source,out):
    dataset=json.loads(source.read_text());rows=[]
    with tempfile.TemporaryDirectory() as tmp:
        store=ProjectStore(Path(tmp)/'projects.sqlite3');labels={}
        for item in dataset['projects']:
            doc=store.save('evaluation',{k:v for k,v in item.items() if k!='id'});labels[doc['id']]=item['id']
        docs=store.list('evaluation')
        for case in dataset['cases']:
            start=time.monotonic();scores=local_rank(case['query'],docs)
            hybrid=recall(store,'evaluation',case['query'],ranker=lambda q,d:scores)
            dense=sorted(docs,key=lambda d:(-scores[d['id']],d['id']))
            lexical=sorted(docs,key=lambda d:(-lexical_score(case['query'],document_text(d)),d['id']))
            results={'no_memory':[],
                     'lexical':[labels[d['id']] for d in lexical if lexical_score(case['query'],document_text(d))>0][:3],
                     'dense':[labels[d['id']] for d in dense if scores[d['id']]>=0.50][:3],
                     'hybrid':[labels[d['id']] for d in hybrid['items']]}
            rows.append({**case,'results':results,'cosine':{labels[k]:round(v,4) for k,v in scores.items()},'elapsed_ms':round((time.monotonic()-start)*1000,1)})
            print(json.dumps({'query':case['query'],'hybrid':results['hybrid']},ensure_ascii=False),flush=True)
    metrics={}
    positive=[r for r in rows if r['relevant']];negative=[r for r in rows if not r['relevant']]
    for mode in ('no_memory','lexical','dense','hybrid'):
        metrics[mode]={'top1_hits':sum(bool(r['results'][mode]) and r['results'][mode][0] in r['relevant'] for r in positive),
                       'positive_cases':len(positive),'mean_recall_at_3':sum(len(set(r['results'][mode])&set(r['relevant']))/len(r['relevant']) for r in positive)/len(positive),
                       'negative_no_candidate':sum(not r['results'][mode] for r in negative),'negative_cases':len(negative)}
    for mode in metrics:
        returned=sum(len(r['results'][mode]) for r in rows)
        correct=sum(len(set(r['results'][mode]) & set(r['relevant'])) for r in rows)
        metrics[mode]['returned_candidates']=returned
        metrics[mode]['relevant_candidates']=correct
        metrics[mode]['candidate_precision']=correct/returned if returned else None
    report={'status':'completed','dataset_kind':dataset['kind'],'dataset_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'cases':rows,'metrics':metrics,'external_model_calls':0,'threshold':0.50,'automatic_application':False,
            'interpretation':'No-memory returns no candidates by definition; this is not business task accuracy. Scores are not probabilities. Development labels only.'}
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(metrics))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path('eval/project_semantic_v1.json'));p.add_argument('--out',type=Path,required=True);a=p.parse_args();evaluate(a.source,a.out)
