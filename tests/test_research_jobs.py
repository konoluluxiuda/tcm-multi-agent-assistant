import asyncio
from concurrent.futures import ThreadPoolExecutor
import tempfile
from pathlib import Path
import unittest
from tcm.research_jobs import JobStore,JobConflict,JobBusy,ResearchWorker


class JobTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'jobs.sqlite3';self.now=1000
        self.store=JobStore(self.path,clock=lambda:self.now)

    async def test_idempotency_concurrent_claim_and_isolation(self):
        d,created=self.store.enqueue('alice','q','same-key');self.assertTrue(created)
        same,created=self.store.enqueue('alice','q','same-key');self.assertFalse(created);self.assertEqual(same['id'],d['id'])
        with self.assertRaises(JobConflict):self.store.enqueue('alice','different','same-key')
        with self.assertRaises(JobBusy):self.store.enqueue('alice','q','another-key')
        with self.assertRaises(KeyError):self.store.get('bob',d['id'])
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(self.store.claim,['worker-a','worker-b']))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(self.store.list('bob'),[])

    async def test_lease_expiry_fences_old_worker_and_preserves_events(self):
        d,_=self.store.enqueue('a','q','key');_,running=self.store.claim('old')
        self.now+=31;self.assertIsNone(self.store.claim('new'))
        saved=self.store.get('a',d['id']);self.assertEqual(saved['status'],'interrupted')
        with self.assertRaises(JobConflict):self.store.save('a',running,'old',status='complete')
        reopened=JobStore(self.path,clock=lambda:self.now)
        doc,events=reopened.events('a',d['id'],0)
        self.assertEqual([e['revision'] for e in events],[1,2]);self.assertEqual(doc['reason'],'lease_expired')
        with self.assertRaises(JobConflict):reopened.events('a',d['id'],99)

    async def test_queued_cancel_and_cancel_wins_over_complete(self):
        d,_=self.store.enqueue('a','q','key');self.store.cancel('a',d['id'])
        self.assertIsNone(self.store.claim('w'));self.assertEqual(self.store.get('a',d['id'])['status'],'cancelled')
        d,_=self.store.enqueue('a','q','key2');_,running=self.store.claim('w')
        with self.assertRaises(JobConflict):self.store.delete('a',d['id'])
        self.store.cancel('a',d['id']);self.assertTrue(self.store.heartbeat(d['id'],'w'))
        final=self.store.save('a',running,'w',status='complete',result={'status':'complete'})
        self.assertEqual(final['status'],'cancelled');self.assertIsNone(final['result'])
        self.store.delete('a',d['id'])
        with self.assertRaises(KeyError):self.store.get('a',d['id'])

    async def test_worker_detached_lifecycle_and_remote_cancel(self):
        started=asyncio.Event();stopped=asyncio.Event()
        async def runner(path,q,*,on_state):
            started.set()
            try:await asyncio.Future()
            finally:stopped.set()
        store=JobStore(self.path)
        d,_=store.enqueue('a','q','key')
        worker=ResearchWorker(lambda:JobStore(self.path),lambda:'unused',runner)
        worker.start();self.addAsyncCleanup(worker.stop)
        await asyncio.wait_for(started.wait(),3)
        JobStore(self.path).cancel('a',d['id'])
        await asyncio.wait_for(stopped.wait(),3)
        for _ in range(50):
            if store.get('a',d['id'])['status']=='cancelled':break
            await asyncio.sleep(.02)
        self.assertEqual(store.get('a',d['id'])['status'],'cancelled')

    async def test_shutdown_marks_running_interrupted_and_new_worker_runs_queue(self):
        started=asyncio.Event()
        async def blocked(*a,**kw):started.set();await asyncio.Future()
        store=JobStore(self.path);d,_=store.enqueue('a','q','key')
        worker=ResearchWorker(lambda:JobStore(self.path),lambda:'unused',blocked)
        worker.start();await asyncio.wait_for(started.wait(),3);await worker.stop()
        self.assertEqual(store.get('a',d['id'])['status'],'interrupted')
        queued,_=store.enqueue('a','new','key2')
        async def success(*a,**kw):return {'status':'complete'}
        other=ResearchWorker(lambda:JobStore(self.path),lambda:'unused',success)
        other.start();self.addAsyncCleanup(other.stop)
        for _ in range(100):
            if store.get('a',queued['id'])['status']=='complete':break
            await asyncio.sleep(.02)
        self.assertEqual(store.get('a',queued['id'])['status'],'complete')

    def test_two_processes_cannot_claim_same_job(self):
        import subprocess,sys,json
        from scripts.run_offline_ci import test_environment
        self.store.enqueue('alice','q','key')
        code="import sys,json; from tcm.research_jobs import JobStore; print(json.dumps(JobStore(sys.argv[1],clock=lambda:1000).claim(sys.argv[2])))"
        processes=[subprocess.Popen([sys.executable,'-c',code,str(self.path),worker],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=test_environment()) for worker in ('process-a','process-b')]
        values=[]
        for process in processes:
            stdout,stderr=process.communicate(timeout=20)
            self.assertEqual(process.returncode,0,stderr);values.append(json.loads(stdout))
        self.assertEqual(sum(v is not None for v in values),1)
