"""Freeze, durable per-dispatch budget, interruption, complete denominators."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import eval_requirements_generalization as runner
from tcm.evaluation.ledger import Ledger
from tcm.evaluation.requirements_benchmark import CaseResult,Scenario
from tests.test_requirements_benchmark import scenario,result


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.suite=self.root/'suite.json';self.suite.write_text(json.dumps([scenario().model_dump()]))
        self.out=self.root/'run';runner.prepare(self.out,self.suite,synthetic=True)
    def worker(self,out,s,p):
        r=result();r.policy=p;return r
    def test_prepare_refuses_overwrite(self):
        with self.assertRaises(ValueError):runner.prepare(self.out,self.suite,synthetic=True)
    def test_independent_questions_cannot_change_during_annotation(self):
        author=self.root/'author';author.mkdir()
        s=scenario();s.split='heldout'
        runner._save(author/'authored_questions.json',[{'id':s.id,'turns':[t.model_dump() for t in s.turns]}])
        (author/'AUTHOR.md').write_text('Independent AI author')
        runner._save(author/'author_freeze.json',{'questions_sha256':runner.digest(author/'authored_questions.json'),
            'author_statement_sha256':runner.digest(author/'AUTHOR.md')})
        runner._save(author/'reference_review.json',{'human_identity_reviewed':False})
        self.assertTrue(runner.author_provenance(author,[s]))
        s.turns[0].question='rewritten'
        with self.assertRaisesRegex(ValueError,'differ'):runner.author_provenance(author,[s])
    def test_turn_budget(self):
        raw=scenario().model_dump();raw['turns'].append({'id':'t2','question':'加字段'})
        self.assertEqual(runner._budget([Scenario.model_validate(raw)])['total'],6)
    def test_frozen_question_change_rejected_before_worker(self):
        p=self.out/'questions.private.json';data=json.loads(p.read_text());data[0]['turns'][0]['question']='changed';p.write_text(json.dumps(data))
        with self.assertRaises(ValueError):runner.execute(self.out,worker=self.worker)
    def test_code_change_rejected(self):
        with patch.object(runner,'_code_hashes',return_value={}):
            with self.assertRaises(ValueError):runner.execute(self.out,worker=self.worker)
    def test_data_change_rejected(self):
        with patch.object(runner,'_data_hashes',return_value={}):
            with self.assertRaises(ValueError):runner.execute(self.out,worker=self.worker)
    def test_protocol_and_manifest_change_rejected(self):
        p=self.out/'protocol.json';v=json.loads(p.read_text());v['budget']['total']=999;p.write_text(json.dumps(v))
        with self.assertRaises(ValueError):runner.validate(self.out)
    def test_repeat_refused_and_resume_skips_all_started(self):
        calls=[]
        def worker(out,s,p):calls.append(p);return self.worker(out,s,p)
        runner.execute(self.out,worker=worker)
        with self.assertRaises(ValueError):runner.execute(self.out,worker=worker)
        runner.execute(self.out,worker=worker,resume=True)
        self.assertEqual(calls,['R','G','V'])
    def test_completed_first_result_survives_interruption(self):
        def worker(out,s,p):
            if p=='G':raise KeyboardInterrupt()
            return self.worker(out,s,p)
        with self.assertRaises(KeyboardInterrupt):runner.execute(self.out,worker=worker)
        report=runner.score_run(self.out)
        self.assertEqual(report['by_policy']['R']['supported']['completed'],1)
        self.assertEqual(report['by_policy']['G']['not_run'],1)
        self.assertEqual(report['by_policy']['V']['supported']['total'],1)
        calls=[]
        runner.execute(self.out,resume=True,worker=lambda out,s,p:(calls.append(p) or self.worker(out,s,p)))
        self.assertEqual(calls,['V'])
    def test_missing_combinations_are_in_denominator(self):
        report=runner.score_run(self.out)
        self.assertEqual(len(report['scores']),3)
        self.assertTrue(all(x['run_status']=='not_run' for x in report['scores']))
    def test_real_requires_matching_manifest_authorization(self):
        protocol,scenarios=runner.validate(self.out);protocol['mode']='real'
        with patch.object(runner,'validate',return_value=(protocol,scenarios)):
            with self.assertRaises(PermissionError):runner.execute(self.out,authorize=True,worker=self.worker)
            with self.assertRaises(ValueError):runner.dry_run(self.out)
    def test_private_files_are_exclusive(self):
        with self.assertRaises(FileExistsError):runner._save(self.out/'protocol.json',{})
        self.assertEqual((self.out/'protocol.json').stat().st_mode & 0o777,0o600)
    def test_global_unknown_dispatch_stops_next_combinations(self):
        def worker(out,s,p):
            if p=='G':Ledger(out/'ledger.sqlite3',runner._budget([s])).reserve(s.id,p,'t1')
            return self.worker(out,s,p)
        r=runner.execute(self.out,worker=worker)
        self.assertTrue(r['provider_stopped']);self.assertEqual(r['by_policy']['V']['not_run'],1)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=Ledger(Path(self.tmp.name)/'ledger.db',{'R':0,'G':2,'V':4,'total':6})
    def test_two_v_dispatches_then_third_blocked(self):
        for _ in range(2):
            i=self.ledger.reserve('case','V','t1');self.ledger.received(i,200,{'total_tokens':12})
        with self.assertRaises(RuntimeError):self.ledger.reserve('case','V','t1')
        self.assertEqual(len(self.ledger.dispatches()),2)
    def test_unknown_charge_survives_reopen(self):
        self.ledger.reserve('case','V','t1')
        again=Ledger(self.ledger.path,self.ledger.budget)
        with self.assertRaises(RuntimeError):again.reserve('next','G','t1')
    def test_r_zero_budget(self):
        with self.assertRaises(RuntimeError):self.ledger.reserve('case','R','t1')
    def test_http_error_stops_and_unknown_usage_is_not_zero(self):
        i=self.ledger.reserve('case','G','t1');self.ledger.received(i,400,None)
        self.assertTrue(self.ledger.stopped());self.assertIsNone(self.ledger.dispatches()[0]['usage'])
        with self.assertRaises(RuntimeError):self.ledger.reserve('other','V','t1')
    def test_budget_cannot_change_on_reopen(self):
        with self.assertRaises(ValueError):Ledger(self.ledger.path,{'R':0,'G':10,'V':20,'total':30})
