"""Versioned API evaluation. prepare/score are offline; dry-run uses real ASGI + fake SDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tcm.evaluation.requirements_benchmark import Scenario,CaseResult,score,load_scenarios,digest
from tcm.evaluation.ledger import Ledger

ROOT=Path(__file__).resolve().parents[1]
SUITE_DIR=ROOT/'eval/requirements_generalization_v1'
POLICIES=('R','G','V')
POLICY_LIMITS={'R':0,'G':1,'V':2,'W':3}


def _save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    # Immutable receipts, no write_text overwrite.
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())


def _read_suite(path):
    text=Path(path).read_text()
    return load_scenarios(text if text.lstrip().startswith('[') else '['+','.join(x for x in text.splitlines() if x.strip())+']')


def _code_hashes():
    from scripts.dataops_source_manifest import manifest
    hashes=manifest()['files']
    for p in [*sorted((ROOT/'tcm/evaluation').glob('*.py')),*sorted((ROOT/'scripts').glob('*.py')),ROOT/'tests/dataops_fixture.py']:
        hashes[str(p.relative_to(ROOT))]=digest(p)
    return hashes


def _budget(scenarios, policies=POLICIES):
    if not policies or len(set(policies))!=len(policies) or set(policies)-POLICY_LIMITS.keys():
        raise ValueError('Choose unique supported evaluation policies')
    n=sum(len(s.turns) for s in scenarios)
    limits={p:POLICY_LIMITS[p]*n for p in policies}
    return {**limits,'total':sum(limits.values()),'turns':n,'scenarios':len(scenarios)}


def _data_hashes(release):
    if release is None:return {'synthetic_fixture':digest(ROOT/'tests/dataops_fixture.py')}
    release=Path(release).resolve()
    config=json.loads((release/'sources.private.json').read_text())['config']
    paths={p for p in release.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    for value in config.values():
        if not isinstance(value,str):continue
        p=Path(value)
        if p.is_file():paths.add(p)
        elif p.is_dir():paths.update(x for x in p.rglob('*') if x.is_file())
    if not paths:raise ValueError('Missing source release')
    if any(p.is_symlink() for p in paths):raise ValueError('Source symlinks require explicit resolution')
    return {str(p.resolve()):digest(p) for p in sorted(paths)}


def _runtime_versions():
    from importlib.metadata import version
    return {'python':sys.version,**{name:version(name) for name in ('langgraph','langchain-openai','pydantic','httpx','fastapi','openpyxl','pymupdf')}}


def _fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def author_provenance(directory,scenarios):
    directory=Path(directory)
    freeze=json.loads((directory/'author_freeze.json').read_text())
    if digest(directory/'authored_questions.json')!=freeze['questions_sha256'] or digest(directory/'AUTHOR.md')!=freeze['author_statement_sha256']:
        raise ValueError('Independent author originals changed')
    raw=json.loads((directory/'authored_questions.json').read_text())
    if [(x['id'],x['turns']) for x in raw]!=[(s.id,[t.model_dump() for t in s.turns]) for s in scenarios]:
        raise ValueError('Annotated questions differ from author originals')
    if any(s.split!='heldout' for s in scenarios):raise ValueError('Independent suite must be heldout')
    return {'freeze':freeze,'questions':raw,'author_statement':(directory/'AUTHOR.md').read_text(),
            'reference_review':json.loads((directory/'reference_review.json').read_text())}


def prepare(out,suite_path=None,*,release=None,synthetic=False,author_dir=None,policies=POLICIES):
    out=Path(out)
    _budget([],policies)
    if out.exists():raise ValueError('run directory already exists')
    scenarios=_read_suite(suite_path or SUITE_DIR/'questions.private.jsonl')
    if not synthetic and release is None:raise ValueError('A real release or explicit synthetic mode is required')
    if synthetic and any(s.split!='dev' for s in scenarios):raise ValueError('Synthetic runs only accept dev scenarios')
    if not synthetic and any(s.split=='heldout' and s.expected.task_class=='supported' and not s.expected.source_assertions for s in scenarios):
        raise ValueError('Heldout supported questions require source witnesses')
    provenance=author_provenance(author_dir,scenarios) if author_dir else None
    out.mkdir(parents=True,mode=0o700)
    if provenance:_save(out/'author_provenance.private.json',provenance)
    _save(out/'questions.private.json',[s.model_dump() for s in scenarios])
    data=_data_hashes(release)
    _save(out/'data.private.json',data)
    protocol={'version':'requirements-generalization-v2','mode':'synthetic' if synthetic else 'real',
        'release':str(Path(release).resolve()) if release else None,'policies':list(policies),'budget':_budget(scenarios,policies),
        'endpoint':'https://api.deepseek.com','model':'deepseek-flash','thinking':'disabled','temperature':0,
        'max_output_tokens':1200,'max_request_bytes':65536,'timeout_seconds':30,'retries':0,
        'trace_export':False,'stop':'unknown dispatch or provider HTTP error stops subsequent combinations',
        'inputs':'current question, static tools/prompts, bounded candidate names, parent fields and correction feedback; no corpus/PDF/history',
        'independent_heldout':bool(provenance),'reference_status':'programmatic source consistency; botanical identity not human approved' if provenance else 'author supplied; must be independently checked before heldout claims'}
    if 'W' in policies:
        protocol['inputs']+='; W additionally sends bounded availability states and mechanical preflight feedback, never source values'
        protocol['feedback_limits']={'model_calls_per_turn':3,'repairs_per_turn':1,'probe_objects':2,'probe_fields_per_object':4,'probe_rounds_at_most':2}
    _save(out/'protocol.json',protocol)
    manifest={'files':{n:digest(out/n) for n in ('questions.private.json','data.private.json','protocol.json')},'code':_code_hashes(),'runtime':_runtime_versions()}
    if provenance:manifest['files']['author_provenance.private.json']=digest(out/'author_provenance.private.json')
    _save(out/'manifest.json',manifest)
    _save(out/'preflight.json',{'budget':protocol['budget'],'manifest_sha256':digest(out/'manifest.json'),'source_files':len(data),'real_model_requests':0})
    return protocol


def validate(out):
    out=Path(out);m=json.loads((out/'manifest.json').read_text());p=json.loads((out/'preflight.json').read_text())
    if digest(out/'manifest.json')!=p['manifest_sha256']:raise ValueError('Manifest changed')
    if any(digest(out/n)!=v for n,v in m['files'].items()):raise ValueError('Frozen input changed')
    if _code_hashes()!=m['code']:raise ValueError('Frozen implementation changed')
    if _runtime_versions()!=m['runtime']:raise ValueError('Frozen runtime changed')
    protocol=json.loads((out/'protocol.json').read_text())
    if _data_hashes(protocol['release'])!=json.loads((out/'data.private.json').read_text()):raise ValueError('Frozen data changed')
    scenarios=_read_suite(out/'questions.private.json')
    if _budget(scenarios,protocol['policies'])!=protocol['budget']:raise ValueError('Budget mismatch')
    return protocol,scenarios


def _run_worker(out,scenario,policy):
    from scripts.run_offline_ci import test_environment
    protocol=json.loads((out/'protocol.json').read_text())
    env=test_environment()
    # Environment credentials are only copied for an explicitly authorized real execution.
    if protocol['mode']=='real':
        from dotenv import dotenv_values
        conf=dotenv_values(ROOT/'.env')
        for key in ('MODEL','BASE_URL','API_KEY'):
            value=os.getenv('RAG_'+key) or conf.get('RAG_'+key) or conf.get('CHAT_'+key)
            if not value:raise ValueError('Missing configured research model')
            env['RAG_'+key]=value
        if env['RAG_MODEL']!=protocol['model'] or env['RAG_BASE_URL'].rstrip('/') not in (protocol['endpoint'],protocol['endpoint']+'/v1'):
            raise ValueError('Provider differs from protocol')
    folder=out/'cases'/f'{scenario.id}_{policy}';folder.mkdir(parents=True,exist_ok=False,mode=0o700)
    with (folder/'worker.log').open('x') as log:
        p=subprocess.run([sys.executable,'-m','tcm.evaluation.api_runner','--out',str(out),'--case',scenario.id,'--policy',policy],
                         cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240*len(scenario.turns))
    if p.returncode:raise RuntimeError('API worker failed; inspect private worker log')
    return CaseResult.model_validate(json.loads((folder/'result.private.json').read_text()))


def _execute(out,authorize=False,*,resume=False,worker=None,synthetic_only=False):
    out=Path(out).resolve();protocol,scenarios=validate(out)
    if synthetic_only and protocol['mode']!='synthetic':raise ValueError('dry-run refuses real providers')
    if protocol['mode']=='real' and authorize!=digest(out/'manifest.json'):raise PermissionError('Pass the reviewed manifest SHA256 to --authorize-execute')
    ledger=Ledger(out/'ledger.sqlite3',protocol['budget'])
    if ledger.results() and not resume:raise ValueError('Execution already started; explicit resume only skips started combinations')
    for s in scenarios:
        for policy in protocol['policies']:
            if ledger.stopped():break
            if not ledger.claim(s.id,policy):continue
            try:r=(worker or _run_worker)(out,s,policy)
            except Exception as exc:
                from tcm.evaluation.requirements_benchmark import ResultStep
                partial=[]
                for turn in s.turns:
                    receipt=out/'cases'/f'{s.id}_{policy}'/f'{turn.id}-observation.private.json'
                    if receipt.exists():partial.append(ResultStep.model_validate(json.loads(receipt.read_text())))
                r=CaseResult(scenario_id=s.id,policy=policy,status='failed',steps=partial,error=type(exc).__name__)
            if r.scenario_id!=s.id or r.policy!=policy:raise ValueError('Worker returned another combination')
            ledger.finish(s.id,policy,r.model_dump())
    # Persistent source/implementation check after execution; retain partial journal if drift occurred.
    validate(out)
    return score_run(out)


def execute(out,authorize=False,*,resume=False,worker=None,synthetic_only=False):
    import fcntl
    # Single coordinator per run; providers and cases are serialized by design.
    with (Path(out)/'execution.lock').open('a') as lock:
        try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another coordinator is running') from None
        return _execute(out,authorize,resume=resume,worker=worker,synthetic_only=synthetic_only)


def dry_run(out,*,resume=False):return execute(out,resume=resume,synthetic_only=True)


def score_run(out):
    out=Path(out);protocol,scenarios=validate(out)
    ledger=Ledger(out/'ledger.sqlite3',protocol['budget']);observed={}
    for raw in ledger.results():
        r=CaseResult.model_validate(raw);key=(r.scenario_id,r.policy)
        if key in observed:raise ValueError('Duplicate combination')
        observed[key]=r
    expected={(s.id,p) for s in scenarios for p in protocol['policies']}
    if set(observed)-expected:raise ValueError('Unknown combination')
    graded=[];by={}
    for s in scenarios:
        for p in protocol['policies']:
            r=observed.get((s.id,p),CaseResult(scenario_id=s.id,policy=p,status='not_run'))
            # Results only reference artifacts in their own case folder.
            folder=(out/'cases'/f'{s.id}_{p}').resolve()
            for step in r.steps:
                for path in (step.artifact_path,step.parent_artifact_path):
                    if path and not Path(path).resolve().is_relative_to(folder):raise ValueError('Artifact outside its combination')
            g=score(s,r).model_dump();g['run_status']=r.status;graded.append(g)
            b=by.setdefault(p,{'supported':{'completed':0,'total':0},'boundary':{'completed':0,'total':0},'not_run':0,'clarification':0})
            b[s.expected.task_class]['total']+=1;b[s.expected.task_class]['completed']+=int(g['completed'])
            b['not_run']+=int(r.status in ('not_run','unknown'));b['clarification']+=int(g['terminal_state']=='clarification')
    requests=ledger.dispatches()
    import math
    efficiency={}
    for policy in protocol['policies']:
        timings=sorted(sum(s.duration_seconds for s in r.steps) for r in observed.values() if r.policy==policy and r.steps)
        receipts=[x for x in requests if x['policy']==policy]
        efficiency[policy]={'dispatches':len(receipts),'unknown_usage':sum(x['usage'] is None for x in receipts),
            'known_tokens':sum((json.loads(x['usage']) or {}).get('total_tokens',0) for x in receipts if x['usage']),
            'timed_cases':len(timings),'p50_seconds':timings[math.ceil(len(timings)*.5)-1] if timings else None,
            'p95_seconds':timings[math.ceil(len(timings)*.95)-1] if timings else None,
            'actual_policies':sorted({s.actual_policy or 'unknown' for r in observed.values() if r.policy==policy for s in r.steps})}

    report={'version':protocol['version'],'mode':protocol['mode'],'by_policy':by,'scores':graded,
        'efficiency':efficiency,'dispatched':len(requests),'real_model_requests':len(requests) if protocol['mode']=='real' else 0,
        'unknown_outcomes':sum(x['status']=='unknown' for x in requests),
        'usage_unknown':sum(x['usage'] is None for x in requests),
        'known_total_tokens':sum((json.loads(x['usage']) or {}).get('total_tokens',0) for x in requests if x['usage']),
        'provider_stopped':ledger.stopped(),'independent_generalization_proven':False}
    report_dir=out/'reports';report_dir.mkdir(exist_ok=True)
    # Each scoring attempt is a new receipt.
    _save(report_dir/f'{len(list(report_dir.glob("*.json")))+1:04d}.json',report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('prepare','dry-run','execute','score'));p.add_argument('--out',type=Path,required=True)
    p.add_argument('--suite',type=Path);p.add_argument('--release',type=Path);p.add_argument('--synthetic',action='store_true')
    p.add_argument('--policies',nargs='+',choices=tuple(POLICY_LIMITS),default=POLICIES,help='R local, G one-call fallback, V blind review, W bounded tool feedback; prepare only')
    p.add_argument('--author-dir',type=Path,help='Original independent questions, freeze and source review')
    p.add_argument('--authorize-execute',help='SHA256 of the reviewed frozen manifest');p.add_argument('--resume',action='store_true')
    a=p.parse_args(argv)
    if a.phase=='prepare':r=prepare(a.out,a.suite,release=a.release,synthetic=a.synthetic,author_dir=a.author_dir,policies=a.policies)
    elif a.phase=='dry-run':r=dry_run(a.out,resume=a.resume)
    elif a.phase=='execute':r=execute(a.out,authorize=a.authorize_execute,resume=a.resume)
    else:r=score_run(a.out)
    print(json.dumps({k:v for k,v in r.items() if k not in ('scores',)},ensure_ascii=False))
    if a.phase in ('execute','dry-run','score'):
        return 0 if all(s['completed'] for s in r['scores']) else 1
    return 0

if __name__=='__main__':raise SystemExit(main())
