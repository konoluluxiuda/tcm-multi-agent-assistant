"""Loopback acceptance: disconnect, cancel, cursor replay and real process crash."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from scripts.run_offline_ci import test_environment
from tcm.research_jobs import JobStore


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tcm-job-http-') as temp:
        root=Path(temp);db=root/'jobs.sqlite3'
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env=test_environment();env.update(ENABLE_RESEARCH_TASKS='1',ETCM_DISEASE_DB='fixture-only',RESEARCH_JOB_DB=str(db))
        log=(root/'server.log').open('w');process=None
        def start():
            proc=subprocess.Popen([sys.executable,'-m','uvicorn','tests.research_jobs_http_fixture:app','--host','127.0.0.1','--port',str(port)],env=env,stdout=log,stderr=subprocess.STDOUT)
            for _ in range(100):
                try:
                    if httpx.get(base+'/health',trust_env=False).status_code==200:return proc
                except httpx.HTTPError:pass
                if proc.poll() is not None:raise RuntimeError('Acceptance server exited')
                time.sleep(.1)
            proc.kill();proc.wait();raise RuntimeError('Acceptance startup timeout')
        with httpx.Client(base_url=base,trust_env=False,timeout=10,cookies={'chat':'acceptance-owner'}) as client:
            def submit(question,key):
                r=client.post('/api/research/jobs',json={'disease':question,'request_key':key});assert r.status_code==202,r.text
                return r.json()['run_id']
            def get(identifier):
                r=client.get('/api/research/jobs/'+identifier);r.raise_for_status();return r.json()
            def wait_status(identifier,status,timeout=10):
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    doc=get(identifier)
                    if doc['status']==status:return doc
                    time.sleep(.1)
                raise AssertionError((status,get(identifier)))
            try:
                process=start()
                identifier=submit('disconnect','disconnect-1')
                first=None
                with client.stream('GET','/api/research/jobs/'+identifier+'/events') as stream:
                    for line in stream.iter_lines():
                        if line.startswith('id: '):first=int(line[4:]);break
                assert first is not None
                done=wait_status(identifier,'complete')
                replay=client.get('/api/research/jobs/'+identifier+'/events',headers={'Last-Event-ID':str(first)})
                ids=[int(line[4:]) for line in replay.text.splitlines() if line.startswith('id: ')]
                assert ids and all(i>first for i in ids) and 'event: done' in replay.text
                print('disconnect and SSE replay passed',flush=True)
                identifier=submit('cancel','cancel-1');wait_status(identifier,'running')
                r=client.post('/api/research/jobs/'+identifier+'/cancel');assert r.status_code==200
                wait_status(identifier,'cancelled')
                identifier=submit('crash','crash-01');wait_status(identifier,'running')
                process.kill();process.wait(timeout=5);process=None
                # Queue another owner's job while the server is down.
                queued,_=JobStore(db).enqueue('offline-owner','queued after shutdown','queued-key')
                print('actual worker process killed; waiting for 30s lease expiry',flush=True)
                time.sleep(31)
                process=start();wait_status(identifier,'interrupted')
                deadline=time.monotonic()+10
                while time.monotonic()<deadline and JobStore(db).get('offline-owner',queued['id'])['status']!='complete':time.sleep(.1)
                assert JobStore(db).get('offline-owner',queued['id'])['status']=='complete'
                report={'status':'passed','sse_disconnect_keeps_job':True,'last_event_id_replay':True,'explicit_cancel':True,
                        'actual_process_kill':True,'expired_run_interrupted_not_reexecuted':True,'queued_job_runs_after_restart':True,'external_model_calls':0}
                args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
            finally:
                if process:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                log.close()

if __name__=='__main__':main()
