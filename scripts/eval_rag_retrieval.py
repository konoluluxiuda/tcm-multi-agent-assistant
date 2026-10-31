"""R2 offline build, development comparison, protocol freeze and reserved first run.

Never imports application config/dotenv. All outbound sockets are denied.
The benchmark scorer sees references; ReleaseSearch receives only each question.
"""
import argparse
from datetime import datetime, timezone
from importlib.metadata import version
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_revisions import fingerprint
from tcm.rag_benchmark import read, write, inside
from tcm.rag_retrieval import METHODS, POLICY, ReleaseSearch, MultiEntryQdrant
from tcm.rag_retrieval_eval import score, summarize

ROOT = Path(__file__).resolve().parents[1]
CODE = ('tcm/rag_retrieval.py','tcm/rag_retrieval_eval.py','scripts/eval_rag_retrieval.py',
        'tcm/mineru_retrieval.py','tcm/mineru_guards.py','tcm/local_dense.py','tcm/qdrant_dense.py',
        'tcm/rag_benchmark.py','tcm/mineru_revisions.py','tcm/herbs.py')
SETTINGS = {'methods':list(METHODS), 'k':10, 'policy':POLICY, 'repeats':1,
            'order':'case_then_method', 'latency_budget_ms':5000,
            'retrieval_backend':'exclusive_local_Qdrant_cosine', 'external_model_calls':0,
            'rerank':'omitted_unless_development_has_ranking_gap',
            'default':'exact_for_explicit_fields_else_bm25_candidates',
            'metrics':'Hit@5/10_and_MRR@10_are_document_only; JSON_and_multi_span_coverage_separate',
            'review':'author_AI_source_checked_pending_human_signoff_not_independent_blind',
            'exhaustive_relevance':False}


def deny_network(attempts):
    def deny(*args, **kwargs):
        attempts.append('blocked')
        raise RuntimeError('R2 forbids outbound networking')
    socket.getaddrinfo = deny
    for key in ('connect','connect_ex','sendto'): setattr(socket.socket,key,deny)


def preflight(release, python):
    # R1 verifier also checks original PDFs; its existing main environment has
    # PyMuPDF. The optional ONNX environment need not install the API stack.
    env = {k:os.environ[k] for k in ('PATH','HOME','LANG','SYSTEMROOT','TMP','TEMP') if k in os.environ}
    code = ('import socket,json,sys; '
            'deny=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError("offline verifier")); '
            'socket.getaddrinfo=deny; socket.socket.connect=deny; socket.socket.connect_ex=deny; '
            'socket.socket.sendto=deny; from tcm.data_release import check; '
            'print(json.dumps(check(sys.argv[1])))')
    proc = subprocess.run([str(python),'-c',code,str(release)],cwd=ROOT,env=env,
                          text=True,capture_output=True,timeout=120,check=True)
    result = json.loads(proc.stdout)
    if result['status'] != 'passed': raise ValueError('R1 integrity check failed')
    return result


def bindings(args):
    release = read(args.release/'release.json')
    return {'release_content_id':release['content_id'], 'release_sha256':digest(args.release/'release.json'),
            'references_sha256':digest(args.release/'benchmark_references.json'),
            'code_sha256':{n:digest(ROOT/n) for n in CODE},
            'asset_sha256':{n:digest(args.assets/n) for n in
                             ('dense/manifest.json','dense/items.json','dense/vectors.npy','qdrant/receipt.json')},
            'runtime':{n:version(n) for n in ('numpy','onnxruntime','tokenizers','qdrant-client')},
            'settings':SETTINGS}


def open_backend(args):
    from tcm.local_dense import LocalEncoder, DenseRetriever
    from tcm.mineru_retrieval import ResearchRetriever
    lexical = ResearchRetriever(args.release/'index')
    try:
        encoder = LocalEncoder(args.model)
        dense = DenseRetriever(lexical,encoder,args.assets/'dense')
        backend = MultiEntryQdrant(dense,args.assets/'qdrant')
        try:
            search = ReleaseSearch(args.release,backend)
        except Exception:
            backend.close(); raise
        return search, backend, lexical
    except Exception:
        lexical.close(); raise


def claim_reserved(release, protocol_path, current, out):
    protocol = read(protocol_path)
    if protocol['bindings'] != current:
        raise ValueError('Frozen code/config/asset/runtime changed; reserved run prohibited')
    # One ledger for this release's reserved set in the common release
    # directory, independent of output/protocol name. Failed attempts also
    # consume first-run status. Do not automatically retry them.
    ledger = release.parent/('reserved_'+current['references_sha256']+'.json')
    write(ledger, {'started_at':datetime.now(timezone.utc).isoformat(),
                   'protocol_sha256':digest(protocol_path), 'out':str(out),
                   'status':'consumed_first_run_even_if_interrupted'})


def evaluate(args, attempts, integrity):
    if args.out.exists(): raise FileExistsError('Use a fresh experiment directory')
    current = bindings(args)
    if args.split == 'reserved':
        claim_reserved(args.release,args.protocol,current,args.out)
    args.out.mkdir(parents=True,exist_ok=False)
    write(args.out/'started.json',{'split':args.split,'bindings':current,'preflight':integrity})
    reference = read(args.release/'benchmark_references.json')
    cases = [c for c in reference['cases'] if c['split'] == args.split]
    registry = read(args.release/'sources.private.json')
    blocks = {b['id']:b for b in read(Path(registry['config']['base'])/'blocks.json')}
    rows = {m:[] for m in METHODS}
    search, backend, lexical = open_backend(args)
    try:
        for case in cases:
            for method in METHODS:
                started = time.monotonic()
                try:
                    result = search.search(case['question'],method,k=SETTINGS['k'])
                except Exception as exc:
                    result = {'status':'error','hits':[],'structured_hits':[],'error_type':type(exc).__name__,
                              'latency_ms':round((time.monotonic()-started)*1000,3)}
                write(args.out/(case['case_id']+'_'+method+'.json'),result)
                metric = score(case,result,blocks)
                metric['within_latency_budget'] = metric['latency_ms'] <= SETTINGS['latency_budget_ms']
                rows[method].append(metric)
    finally:
        search.close()
        try: backend.close()
        finally: lexical.close()
    if bindings(args) != current: raise ValueError('Experiment inputs changed during execution')
    final_integrity = preflight(args.release,args.verify_python)
    summary = {'split':args.split, 'bindings':current,
               'summary':{m:summarize(v) for m,v in rows.items()}, 'cases':rows,
               'final_integrity':final_integrity,
               'network_attempts':len(attempts), 'external_calls':0, 'generation_calls':0,
               'human_signoff':False, 'relevance_complete':False, 'recall':None,
               'latency':'single warm process, first query includes inference warmup; initialization excluded; not production p95'}
    write(args.out/'summary.json',summary)
    return {k:v for k,v in summary.items() if k not in ('bindings','cases')}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('build','evaluate','freeze','query'))
    p.add_argument('--release',type=Path,default=Path('var/data_releases/r1_20261030'))
    p.add_argument('--assets',type=Path,default=Path('var/rag_r2_v1'))
    p.add_argument('--model',type=Path,default=Path('var/evidence_assistant_v1/local_bge_small_zh'))
    p.add_argument('--verify-python',type=Path,default=Path('.venv/bin/python'))
    p.add_argument('--out',type=Path)
    p.add_argument('--split',choices=('development','reserved'),default='development')
    p.add_argument('--protocol',type=Path,default=Path('var/rag_r2_v1/protocol.json'))
    p.add_argument('--development-report',type=Path)
    p.add_argument('--question')
    p.add_argument('--method',choices=METHODS,default='bm25')
    args=p.parse_args()
    attempts=[];deny_network(attempts)
    integrity=preflight(args.release,args.verify_python)
    if args.mode == 'build':
        from tcm.local_dense import LocalEncoder,DenseRetriever,build_vectors
        from tcm.qdrant_dense import build_generation
        from tcm.mineru_retrieval import ResearchRetriever
        r=ResearchRetriever(args.release/'index')
        try:
            encoder=LocalEncoder(args.model)
            receipt=build_vectors(r,encoder,args.assets/'dense')
            qreceipt=build_generation(DenseRetriever(r,encoder,args.assets/'dense'),args.assets/'qdrant')
            result={'preflight':integrity,'dense':receipt,'qdrant_count':qreceipt['count']}
            write(args.assets/'build_receipt.json',result)
        finally:r.close()
    elif args.mode == 'freeze':
        if not args.development_report: p.error('--development-report is required')
        dev=read(args.development_report)
        current=bindings(args)
        if dev['split']!='development' or dev['bindings']!=current or dev['network_attempts']:
            raise ValueError('Need a completed development comparison for the exact frozen inputs')
        if any(v['errors'] for v in dev['summary'].values()):
            raise ValueError('Resolve development execution errors before freezing')
        result={'kind':'R2_frozen_before_reserved_first_run','created_at':datetime.now(timezone.utc).isoformat(),
                'bindings':current,'development_sha256':digest(args.development_report),
                'reserved_runs':1,'no_automatic_retry':True}
        write(args.protocol,result)
    elif args.mode == 'query':
        if not args.question:p.error('--question is required')
        if args.method in ('exact','bm25'):
            search=ReleaseSearch(args.release)
            try:result=search.search(args.question,args.method)
            finally:search.close()
        else:
            search,backend,lexical=open_backend(args)
            try: result=search.search(args.question,args.method)
            finally:
                search.close()
                try:backend.close()
                finally:lexical.close()
    else:
        if args.out is None:p.error('--out is required')
        result=evaluate(args,attempts,integrity)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
