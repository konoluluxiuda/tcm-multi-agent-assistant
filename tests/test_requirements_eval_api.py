"""Real ASGI/SQLite/XLSX chains, independent fixed SDK fixture, no provider."""
import json
from pathlib import Path
import tempfile
import unittest
from scripts import eval_requirements_generalization as runner
from tcm.evaluation.api_runner import run,observe,_bind_real
from tcm.evaluation.requirements_benchmark import score,digest
from tcm.evaluation.ledger import Ledger


class ApiTests(unittest.TestCase):
    def test_v_query_cross_source_and_parent_append_real_deliveries(self):
        suite=runner._read_suite('eval/requirements_generalization_v1/dev_synthetic.json')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);out=root/'run';runner.prepare(out,'eval/requirements_generalization_v1/dev_synthetic.json',synthetic=True)
            for scenario in suite[:3]:
                with self.subTest(scenario=scenario.id):
                    folder=out/'cases'/f'{scenario.id}_V';folder.mkdir(parents=True)
                    r=run(out,scenario,'V')
                    g=score(scenario,r)
                    self.assertTrue(g.completed,g.model_dump())
                    if scenario.id=='D002':self.assertEqual(len(r.steps[0].answer_steps),2)
                    if scenario.id=='D003':
                        self.assertEqual(len(r.steps),2)
                        self.assertEqual(r.steps[1].parent_artifact_before,r.steps[1].parent_artifact_after)
                        from openpyxl import load_workbook
                        wb=load_workbook(r.steps[1].artifact_path);wb['结果']['D2']='bad';wb.save(r.steps[1].artifact_path);wb.close()
                        r.steps[1].artifact_sha256=digest(r.steps[1].artifact_path)
                        self.assertFalse(score(scenario,r).completed)
            ledger=Ledger(out/'ledger.sqlite3',runner._budget(suite))
            self.assertEqual(len(ledger.dispatches()),8)
            self.assertFalse(ledger.stopped())

    def test_chat_query_observation_uses_response_not_reference(self):
        suite=runner._read_suite('eval/requirements_generalization_v1/dev_synthetic.json')
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'run';runner.prepare(out,'eval/requirements_generalization_v1/dev_synthetic.json',synthetic=True)
            s=suite[-1];(out/'cases'/f'{s.id}_R').mkdir(parents=True)
            r=run(out,s,'R');self.assertTrue(score(s,r).completed)
            self.assertEqual(len(Ledger(out/'ledger.sqlite3',runner._budget(suite)).dispatches()),0)

    def test_provider_hook_reserves_before_transport_and_blocks_third(self):
        import httpx
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);ledger=Ledger(folder/'ledger.db',{'R':0,'G':1,'V':2,'total':3});sent=[]
            def transport(req):
                sent.append(req);self.assertEqual(ledger.dispatches()[-1]['status'],'unknown')
                return httpx.Response(200,json={'usage':{'total_tokens':10}})
            http=httpx.Client(transport=httpx.MockTransport(transport))
            m=SimpleNamespace(http_client=http)
            _bind_real(m,ledger,'case','V',{'turn':'t1'},{'max_request_bytes':65536,'model':'deepseek-flash','temperature':0,'thinking':'disabled','max_output_tokens':1200},folder)
            for _ in range(2):http.post('https://api.deepseek.com/chat/completions',json={'messages':[],'model':'deepseek-flash','temperature':0,'thinking':{'type':'disabled'},'max_completion_tokens':1200,'stream':False})
            with self.assertRaises(RuntimeError):http.post('https://api.deepseek.com/chat/completions',json={'messages':[],'model':'deepseek-flash','temperature':0,'thinking':{'type':'disabled'},'max_completion_tokens':1200,'stream':False})
            self.assertEqual(len(sent),2);http.close()

    def test_timeout_keeps_unknown_dispatch_and_blocks_retry(self):
        import httpx
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);ledger=Ledger(folder/'ledger.db',{'R':0,'G':1,'V':2,'total':3})
            def transport(req):raise httpx.ReadTimeout('synthetic timeout')
            http=httpx.Client(transport=httpx.MockTransport(transport))
            _bind_real(SimpleNamespace(http_client=http),ledger,'case','V',{'turn':'t1'},{'max_request_bytes':65536,'model':'deepseek-flash','temperature':0,'thinking':'disabled','max_output_tokens':1200},folder)
            with self.assertRaises(httpx.ReadTimeout):http.post('https://api.deepseek.com/chat/completions',json={'model':'deepseek-flash','temperature':0,'thinking':{'type':'disabled'},'max_completion_tokens':1200,'stream':False})
            with self.assertRaises(RuntimeError):http.post('https://api.deepseek.com/chat/completions',json={'model':'deepseek-flash','temperature':0,'thinking':{'type':'disabled'},'max_completion_tokens':1200,'stream':False})
            self.assertEqual(len(ledger.dispatches()),1);http.close()
