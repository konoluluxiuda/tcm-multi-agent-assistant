"""Gate sensitivity: an intentional failure must never be counted as a pass."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import check_understanding_counterexamples as gate
from tests.test_understanding_v2 import Sequence
from tests import test_unified_request_harness as legacy
from tcm.dataops.research_question import request_proposal


class GateTests(unittest.TestCase):
    def setUp(self):
        self.suite=json.loads((gate.ROOT/'eval/understanding_v2/counterexamples_gate_v2.json').read_text())

    def test_all_cases_run_including_query_and_parent_paths(self):
        report=gate.evaluate(self.suite)
        self.assertEqual(report['status'],'passed',report)
        self.assertEqual((report['total'],report['matched'],report['skipped']),(17,17,0))

    def test_disabled_guard_is_an_expected_negative_control_not_green(self):
        with patch.dict('os.environ',{'SEMANTIC_REGEX_GUARDS':'0'}):
            report=gate.evaluate(self.suite)
            with contextlib.redirect_stdout(io.StringIO()):code=gate.main([])
        self.assertEqual(report['status'],'failed');self.assertEqual(code,1)
        self.assertTrue({'AC03','AC09','AC10'} <= {r['id'] for r in report['results'] if not r['matched']})

    def test_programmer_errors_do_not_count_as_rejections(self):
        case=self.suite['cases'][1]
        for exc in (KeyError('bug'),TypeError('bug'),ValueError('bug'),RuntimeError('bug')):
            with patch.object(gate,'compile_draft',side_effect=exc):result=gate.run_case(case)
            self.assertFalse(result['matched']);self.assertEqual(result['actual']['outcome'],'error')

    def test_rejecting_for_wrong_reason_or_stage_is_failure(self):
        original=self.suite['cases'][1]
        for changes in ({'reject_code':'some_other_reason'},{'expected_stage':'draft_schema'}):
            result=gate.run_case({**original,**changes});self.assertFalse(result['matched'])

    def test_wrong_label_makes_cli_fail_and_report_is_machine_readable(self):
        value=deepcopy(self.suite);value['cases'][0]['expect']='reject'
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'cases.json';out=Path(folder)/'result.json'
            source.write_text(json.dumps(value))
            with contextlib.redirect_stdout(io.StringIO()):code=gate.main([str(source),'--out',str(out)])
            report=json.loads(out.read_text())
        self.assertEqual(code,1);self.assertEqual(report['status'],'failed')
        self.assertIn('suite_sha256',report);self.assertTrue(report['files_sha256'])


class CoverageTests(unittest.TestCase):
    def call(self,q,args,**kwargs):
        return request_proposal(Sequence(('submit_query_intent',args)),q,{'黄芪':['a']},
                                authorized=True,allow_queries=True,policy='unified_v2',**kwargs)

    def test_profile_cannot_replace_document_query_with_guards_on_or_off(self):
        for flag in ('0','1'):
            with patch.dict('os.environ',{'SEMANTIC_REGEX_GUARDS':flag}):
                result=self.call('黄芪有什么作用',{'action':'herb_profile','entities':['黄芪']})
            self.assertEqual(result['failure']['code'],'query_routed_to_wrong_tool')
            self.assertNotIn('route',result)

    def test_mixed_requirement_is_rejected_after_bounded_correction(self):
        args={'action':'herb_field','entities':['黄芪'],'fields':['medicinal_part']}
        for flag in ('0','1'):
            model=Sequence(('submit_query_intent',args),('submit_query_intent',args))
            with patch.dict('os.environ',{'SEMANTIC_REGEX_GUARDS':flag}):
                result=request_proposal(model,'黄芪的药用部位和功效',{'黄芪':['a']},
                                        authorized=True,allow_queries=True,policy='unified_v2')
            self.assertEqual(result['status'],'needs_input');self.assertEqual(model.calls,2)
            self.assertNotIn('route',result)
            self.assertEqual(json.loads(model.messages[1][1]['content'])['validation_feedback']['code'],'query_requirement_omitted')

    def test_explicit_source_shared_fields_and_profile_keep_working(self):
        for q,args in [('黄芪有什么作用',{'action':'research','entities':['黄芪'],'fields':['功能与主治']}),
                       ('查看ETCM黄芪的性味',{'action':'herb_field','entities':['黄芪'],'fields':['properties','flavors']}),
                       ('黄芪的相关属性',{'action':'herb_profile','entities':['黄芪']})]:
            self.assertEqual(self.call(q,args)['status'],'query',q)

    def test_complete_mixed_plan_remains_available(self):
        args={'action':'analysis_plan','plan':{'steps':[
            {'call':{'tool':'herb_attributes','arguments':{'name':'黄芪','fields':['medicinal_part']}},'clauses':[0]},
            {'call':{'tool':'pharmacopoeia_lookup','arguments':{'name':'黄芪','fields':['功能与主治']}},'clauses':[1]}]}}
        self.assertEqual(self.call('查看黄芪的药用部位，查询黄芪的药典功效',args)['status'],'query')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        legacy.EntryTests.setUp(self)
        p=patch.dict('os.environ',{'DATAOPS_UNDERSTANDING_POLICY':'unified_v2'})
        p.start();self.addCleanup(p.stop)

    def test_bad_profile_never_executes_query_and_replays_zero_calls(self):
        model=Sequence(('submit_query_intent',{'action':'herb_profile','entities':['Alpha']}))
        body={'query':'Alpha有什么作用','request_key':'profile-coverage-negative'}
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/chat',json=body)
            self.assertEqual(first.status_code,200,first.text)
            self.assertEqual(first.json()['assistant']['status'],'clarification_needed')
            self.assertEqual(first.json()['model_calls'],1)
            self.assertNotIn('tcm',first.json())
            again=self.client.post('/chat',json=body)
            self.assertEqual(again.json()['model_calls'],0);self.assertEqual(model.calls,1)
            self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'],[])

    def test_guard_configuration_binds_unexecuted_proposal_receipt(self):
        self.client.get('/dataops/research')
        body={'question':'帮我整理一下资料','request_key':'guard-config-conflict','use_model':False}
        with patch.dict('os.environ',{'SEMANTIC_REGEX_GUARDS':'1'}):
            first=self.client.post('/api/dataops/research-questions',json=body)
        self.assertEqual(first.status_code,200,first.text)
        with patch.dict('os.environ',{'SEMANTIC_REGEX_GUARDS':'0'}):
            second=self.client.post('/api/dataops/research-questions',json=body)
        self.assertEqual(second.status_code,409,second.text)
