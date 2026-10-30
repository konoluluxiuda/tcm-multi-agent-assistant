"""Rehearse existing local demos; no live model calls or new benchmark claims."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def fingerprints():
    paths=[ROOT/'app.py',ROOT/'templates/index.html']
    for directory,pattern in [('tcm','*.py'),('tcm','*.js'),('agents','*.py'),('tests','test_research_loop_api.py'),('tests','test_pair_checkpoint.py'),('tests','test_runtime_backup.py')]:
        paths.extend((ROOT/directory).glob(pattern))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--url',help='Optional loopback preview with --synthetic-loop enabled')
    p.add_argument('--browser-python',type=Path);p.add_argument('--browser',type=Path)
    args=p.parse_args()
    if args.url:
        url=urlsplit(args.url)
        if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1') or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            p.error('--url must be a local HTTP preview root')
        if not args.browser_python or not args.browser:p.error('Browser runtime and executable required with --url')
    args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    before=fingerprints();(args.out/'source_manifest.json').write_text(json.dumps(before,indent=2)+'\n')
    stages=[('contracts',[sys.executable,'-m','unittest','tests.test_research_loop_api','tests.test_pair_checkpoint','tests.test_runtime_backup','-q']),
            ('loop_crashes',[sys.executable,'-m','scripts.check_research_loop_recovery','--out',str(args.out/'loop_crashes')])]
    if args.url:
        base=args.url.rstrip('/')+'/'
        stages.extend([
            ('real_pair_browser',[str(args.browser_python),'-m','scripts.check_pair_research_browser','--url',base,'--browser',str(args.browser),'--out',str(args.out/'real_pair')]),
            ('synthetic_loop_browser',[str(args.browser_python),'-m','scripts.check_research_loop_browser','--url',base,'--browser',str(args.browser),'--out',str(args.out/'loop_browser.json')])])
    results=[]
    for name,command in stages:
        with (args.out/(name+'.log')).open('w') as log:
            child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,start_new_session=True)
            try:code=child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);child.wait();code=124
        results.append({'stage':name,'exit_code':code,'passed':code==0,'log':name+'.log'})
        print(json.dumps(results[-1]),flush=True)
        if code:break
    stable=before==fingerprints()
    report={'status':'passed' if stable and len(results)==len(stages) and all(r['passed'] for r in results) else 'failed',
            'stages':results,'source_files_unchanged':stable,'browser_requested':bool(args.url),
            'live_model_calls':0,'business_accuracy':'not_measured','latency_and_cost_benchmark':'not_measured',
            'limits':['Known development scenarios; not independent users','Historical audit metrics are not recomputed',
                      'Does not verify that an existing preview process loaded the current files',
                      'No new PostgreSQL SIGKILL or full database restore drill in this rehearsal']}
    (args.out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':report['status'],'out':str(args.out)}));return 0 if report['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
