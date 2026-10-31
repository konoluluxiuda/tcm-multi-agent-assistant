"""Create local-only credentials and manage the optional isolated Langfuse stack."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'var/langfuse_local'


def private_write(path, text):
    with path.open('x', encoding='utf-8') as f:
        os.chmod(path, 0o600)
        f.write(text)


def initialize(state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / 'local.env'
    if path.exists():
        from dotenv import dotenv_values
        values = dotenv_values(path)
        if not all(values.get('LF_'+k) for k in ('PORT', 'PUBLIC_KEY', 'SECRET_KEY', 'USER_EMAIL', 'USER_PASSWORD')):
            raise ValueError('Existing local credentials are incomplete; do not regenerate keys over existing volumes')
    else:
        values = {'LF_PORT':'3037', 'LF_MEDIA_PORT':'3038', 'LF_USER_EMAIL':'8n1eld@localhost.invalid',
              'LF_PUBLIC_KEY':'pk-lf-'+secrets.token_hex(16), 'LF_SECRET_KEY':'sk-lf-'+secrets.token_hex(24)}
        for name in ('POSTGRES_PASSWORD','CLICKHOUSE_PASSWORD','REDIS_PASSWORD','MINIO_PASSWORD',
                 'SALT','ENCRYPTION_KEY','NEXTAUTH_SECRET','USER_PASSWORD'):
            values['LF_'+name] = secrets.token_hex(32)
        private_write(path, ''.join(f'{k}={v}\n' for k,v in values.items()))
    if not (state/'app.env').exists():
        private_write(state/'app.env', ''.join(f'{k}={v}\n' for k,v in {
        'LANGFUSE_BASE_URL':'http://127.0.0.1:'+values['LF_PORT'],'LANGFUSE_PUBLIC_KEY':values['LF_PUBLIC_KEY'],
        'LANGFUSE_SECRET_KEY':values['LF_SECRET_KEY'],'ENABLE_LANGFUSE':'1',
        'LANGFUSE_TRACING_ENABLED':'true','TCM_TRACE_CONTENT':'1'}.items()))
    if not (state/'access.json').exists():
        private_write(state/'access.json', json.dumps({'url':'http://localhost:'+values['LF_PORT'],
        'email':values['LF_USER_EMAIL'],'password':values['LF_USER_PASSWORD'],
        'project_id':'tcm-dataops'},indent=2)+'\n')


def docker_command():
    if os.name != 'nt' and Path('/proc/sys/kernel/osrelease').exists() and 'microsoft' in Path('/proc/sys/kernel/osrelease').read_text().lower():
        executable = shutil.which('docker.exe')
        if executable:
            return [executable, '--context', 'desktop-linux'], True
    executable = shutil.which('docker')
    if not executable:
        raise RuntimeError('Docker is unavailable; start Docker Desktop / enable WSL integration')
    return [executable], False


def host_path(path, windows):
    if windows:
        return subprocess.check_output(['wslpath','-w',str(path.resolve())],text=True).strip()
    return str(path.resolve())


def compose(state):
    command, windows = docker_command()
    return command + ['compose','--project-name','tcm-langfuse', '--env-file',host_path(state/'local.env',windows),
                      '-f',host_path(ROOT/'compose.langfuse.yaml',windows)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('init','up','status','down'))
    parser.add_argument('--state',type=Path,default=STATE)
    args = parser.parse_args();state=args.state.resolve()
    # Secrets and state stay under ignored var/ rather than in publication inputs.
    if not state.is_relative_to(ROOT/'var'):
        parser.error('--state must be under project var/')
    initialize(state)
    if args.action=='init':
        print('Prepared local credentials: '+str(state/'access.json'));return
    command=compose(state)
    subprocess.run(command+['config','--quiet'],check=True)
    suffix={'up':['up','-d'], 'status':['ps'], 'down':['down']}[args.action]
    subprocess.run(command+suffix,check=True)
    if args.action=='up':
        print('Langfuse URL: http://localhost:3037 ; credentials: '+str(state/'access.json'))


if __name__=='__main__':main()
