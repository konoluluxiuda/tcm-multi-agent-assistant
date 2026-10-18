"""Back up a running research container; verify restored cookies in an isolated API."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import time
from uuid import uuid4

import httpx

from agents.conversation_store import ConversationStore, SessionExpired
from agents.memory_backup import restore_backup
from scripts.run_offline_ci import ROOT, test_environment


def verify(container, url, output):
    def docker(*args, input=None):
        return subprocess.check_output(['docker', *args], input=input, text=True)
    before = json.loads(docker('inspect', container))[0]
    labels = before['Config']['Labels'] or {}
    if (labels.get('com.docker.compose.project') != 'tcm-research' or
            labels.get('com.docker.compose.service') != 'chat' or not before['State']['Running']):
        raise ValueError('Only a running tcm-research/chat container is allowed')
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1'):
        raise ValueError('Only a local HTTP test URL is allowed')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {'status': 'running', 'scope': 'online backup of running research memory; isolated restore only',
              'live_container_restarted': False, 'live_database_replaced': False, 'checks': []}
    remote = '/tmp/tcm-memory-backup-'+uuid4().hex
    active = httpx.Client(base_url=url, trust_env=False, timeout=30)
    deleted = httpx.Client(base_url=url, trust_env=False, timeout=30)
    server = log = None
    remote_created = False
    try:
        assert active.get('/ready/research').status_code == 200
        for client in (active, deleted):
            reply = client.post('/chat', json={'query': '/research 甘草的功效'})
            assert reply.status_code == 200 and reply.json()['research']['status'] == 'candidates'
        cookie_name = next(k for k in active.cookies.keys() if k == 'research_chat_session')
        active_cookie = active.cookies[cookie_name]; deleted_cookie = deleted.cookies[cookie_name]
        assert deleted.delete('/conversation').status_code == 200
        report['checks'].append('dedicated_active_and_deleted_api_sessions_created')
        # Execute the stdlib-only module over stdin, without installing files or
        # replacing the image/source. No database bytes or cookies go to stdout.
        code = (ROOT/'agents/memory_backup.py').read_text()
        code += '\nprint(json.dumps(create_backup(os.environ["CHAT_MEMORY_DB"], '+repr(remote)+', ttl_seconds=int(os.environ.get("CHAT_MEMORY_TTL_DAYS", "30"))*86400)))\n'
        manifest = json.loads(docker('exec', '-i', container, 'python', '-', input=code))
        remote_created = True
        # Docker Desktop cp may not see tmpfs contents. Stream only these two
        # fixed files through exec; private database bytes never enter logs.
        bundle=output/'bundle'; bundle.mkdir(mode=0o700)
        stream_code = ('import sys,tarfile,pathlib; p=pathlib.Path(sys.argv[1]); '
                       't=tarfile.open(fileobj=sys.stdout.buffer,mode="w|"); '
                       '[t.add(p/name,arcname=name,recursive=False) for name in ("memory.sqlite3","manifest.json")]; t.close()')
        transfer=subprocess.Popen(['docker','exec',container,'python','-c',stream_code,remote],stdout=subprocess.PIPE)
        try:
            names=set()
            with tarfile.open(fileobj=transfer.stdout,mode='r|') as archive:
                for member in archive:
                    if member.name not in ('memory.sqlite3','manifest.json') or not member.isfile() or member.name in names:
                        raise ValueError('Unexpected backup stream entry')
                    names.add(member.name)
                    descriptor=os.open(bundle/member.name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                    with os.fdopen(descriptor,'wb') as target, archive.extractfile(member) as source:
                        import shutil
                        shutil.copyfileobj(source,target)
            assert names=={'memory.sqlite3','manifest.json'}
            assert transfer.wait(timeout=10)==0
        finally:
            transfer.stdout.close()
            if transfer.poll() is None: transfer.terminate(); transfer.wait(timeout=10)
        report['checks'].append('online_backup_includes_signing_key_and_tombstones')
        receipt = restore_backup(output/'bundle', output/'restored.sqlite3')
        restored = ConversationStore(output/'restored.sqlite3', ttl_seconds=manifest['ttl_seconds'])
        assert restored.valid_cookie(active_cookie) and restored.valid_cookie(deleted_cookie)
        snapshot = restored.load_serialized(active_cookie)
        assert snapshot.messages and snapshot.tcm_context
        try: restored.load_serialized(deleted_cookie)
        except SessionExpired: pass
        else: raise AssertionError('Deleted fixture resurrected')
        report['checks'].append('isolated_restore_accepts_old_cookie_and_keeps_deletion')
        env = test_environment()
        env.update(CHAT_MEMORY_DB=str((output/'restored.sqlite3').resolve()), CHAT_COOKIE_NAME=cookie_name,
                   CHAT_MEMORY_TTL_DAYS=str(manifest['ttl_seconds']//86400), ENABLE_PHARMACOPOEIA_RESEARCH='1',
                   PYTHONPYCACHEPREFIX=str((output/'pycache').resolve()))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); restored_url=f'http://127.0.0.1:{sock.getsockname()[1]}'
            log = (output/'restored-server.log').open('w')
            os.chmod(output/'restored-server.log', 0o600)
            server = subprocess.Popen([os.sys.executable, '-m', 'scripts.verify_tcm_chat_ui', '--serve', str(sock.fileno())],
                cwd=ROOT, env=env, pass_fds=(sock.fileno(),), stdout=log, stderr=log)
            with httpx.Client(base_url=restored_url, trust_env=False, timeout=10) as api:
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    try:
                        if api.get('/health').status_code == 200: break
                    except httpx.HTTPError: pass
                    if server.poll() is not None: raise RuntimeError('Isolated server stopped')
                    time.sleep(.1)
                api.cookies.set(cookie_name, active_cookie)
                response=api.get('/conversation')
                history=response.json()
                assert response.status_code == 200 and history['messages']
                assert history['research']['status'] == 'candidates'
                api.cookies.clear(); api.cookies.set(cookie_name, deleted_cookie)
                assert api.get('/conversation').json()['messages'] == []
            report['checks'].append('new_api_process_restores_history_and_rechecks_real_sources')
        assert active.get('/conversation').json()['messages']
        after=json.loads(docker('inspect', container))[0]
        assert before['State']['StartedAt'] == after['State']['StartedAt']
        assert before['RestartCount'] == after['RestartCount']
        assert after['State'].get('Health',{}).get('Status') == 'healthy'
        report['checks'].append('original_service_still_running_with_original_session')
        report.update(status='passed', schema_version=manifest['schema_version'],
                      ttl_seconds=manifest['ttl_seconds'], retention_days=manifest['retention_days'],
                      backup_sha256=manifest['database_sha256'], restore_integrity_validated=True,
                      expired_on_restore=receipt['expired_payloads_removed'])
    finally:
        if server and server.poll() is None: server.terminate(); server.wait(timeout=10)
        if log: log.close()
        # Only the two test sessions created here are cleared. The private bundle
        # still contains the earlier snapshot until its separate retention expiry.
        for client in (active, deleted):
            try: client.delete('/conversation')
            finally: client.close()
        if remote_created:
            docker('exec', container, 'python', '-c', 'import shutil,sys; shutil.rmtree(sys.argv[1])', remote)
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        os.chmod(output/'report.json', 0o600)
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', required=True)
    parser.add_argument('--url', default='http://127.0.0.1:7872')
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    print(json.dumps(verify(args.container,args.url,args.out.resolve()),indent=2))
