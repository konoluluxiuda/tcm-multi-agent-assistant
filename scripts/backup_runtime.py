"""Back up the core runtime offline; restore trusted bundles only to isolated targets."""
import argparse
import json
import os
from pathlib import Path
from tcm.runtime_backup import create_bundle,inspect_bundle,restore_bundle,RuntimeBackupError


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    b=sub.add_parser('backup');b.add_argument('--memory',type=Path,required=True);b.add_argument('--projects',type=Path,required=True)
    b.add_argument('--loops',type=Path,help='Optional persistent loop journal');b.add_argument('--jobs',type=Path);b.add_argument('--out',type=Path,required=True)
    b.add_argument('--runs',type=Path,help='Optional structured research run history')
    b.add_argument('--plans',type=Path,help='Chat plan journal; required for interrupted plan jobs')
    b.add_argument('--evidence',type=Path,help='Required when included runs/loops reference externalized observations')
    b.add_argument('--workers-stopped',action='store_true');b.add_argument('--cookie-name',required=True)
    b.add_argument('--dsn-env',help='Name of environment variable holding the dedicated runtime DSN')
    b.add_argument('--pg-bin',type=Path,default=Path('/usr/bin'))
    i=sub.add_parser('inspect');i.add_argument('--bundle',type=Path,required=True)
    r=sub.add_parser('restore');r.add_argument('--bundle',type=Path,required=True);r.add_argument('--target',type=Path,required=True)
    r.add_argument('--dsn-env');r.add_argument('--pg-bin',type=Path,default=Path('/usr/bin'))
    a=p.parse_args()
    try:
        dsn=os.environ[a.dsn_env] if getattr(a,'dsn_env',None) else None
        if a.command=='backup':
            files={'memory':a.memory,'projects':a.projects}
            if a.jobs:files['jobs']=a.jobs
            if a.loops:files['loops']=a.loops
            if a.runs:files['runs']=a.runs
            if a.plans:files['plans']=a.plans
            if a.evidence:files['evidence']=a.evidence
            result=create_bundle(files,a.out,workers_stopped=a.workers_stopped,dsn=dsn,pg_bin=a.pg_bin,cookie_name=a.cookie_name)
        elif a.command=='inspect':result=inspect_bundle(a.bundle)
        else:result=restore_bundle(a.bundle,a.target,dsn=dsn,pg_bin=a.pg_bin)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (RuntimeBackupError,KeyError) as exc:
        print(json.dumps({'status':'error','error_code':str(exc) if isinstance(exc,RuntimeBackupError) else 'dsn_environment_missing'}));return 2


if __name__=='__main__':raise SystemExit(main())
