import tempfile
from pathlib import Path
import unittest
from tcm.research_jobs import JobStore,JobConflict,JobBusy


class ResumeTests(unittest.TestCase):
    def test_resume_requires_owner_interruption_and_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=JobStore(Path(tmp)/'queue.db')
            doc,_=store.enqueue('owner','query','abcdefgh')
            with self.assertRaises(JobConflict):store.resume('owner',doc['id'])
            _,doc=store.claim('worker')
            doc=store.save('owner',{**doc,'recovery':'checkpoint'},'worker',status='interrupted')
            with self.assertRaises(KeyError):store.resume('other',doc['id'])
            resumed=store.resume('owner',doc['id'])
            self.assertEqual(resumed['status'],'queued');self.assertGreater(resumed['revision'],doc['revision'])
            with self.assertRaises(JobConflict):store.resume('owner',doc['id'])
            with self.assertRaises(JobConflict):store.save('owner',doc,'worker',status='complete')

    def test_cancelled_and_noncheckpoint_jobs_are_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=JobStore(Path(tmp)/'queue.db')
            for i,status in enumerate(('cancelled','interrupted')):
                doc,_=store.enqueue('owner','query',str(i)*8)
                _,doc=store.claim('w');store.save('owner',doc,'w',status=status)
                with self.assertRaises(JobConflict):store.resume('owner',doc['id'])

    def test_active_owner_blocks_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=JobStore(Path(tmp)/'queue.db')
            doc,_=store.enqueue('owner','query','abcdefgh');_,doc=store.claim('w')
            store.save('owner',{**doc,'recovery':'checkpoint'},'w',status='interrupted')
            store.enqueue('owner','different','ijklmnop')
            with self.assertRaises(JobBusy):store.resume('owner',doc['id'])


if __name__=='__main__':unittest.main()
