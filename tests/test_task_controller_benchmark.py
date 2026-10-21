"""Comparison integrity; test doubles never become live model measurements."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.benchmark_task_controllers import (CASES,POLICIES,Calls,MeteredSDK,OfflineSDK,
    prepare,run_row,plan_once,score,main)
from tcm.research_task_store import ResearchTaskStore


class ControllerBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ResearchTaskStore(Path(self.tmp.name)/'tasks.sqlite3')

    def test_all_three_policies_share_expected_cases_with_offline_mock_explicit(self):
        calls=Calls()
        for case in CASES:
            for policy in POLICIES:
                row=run_row(self.store,case,policy,OfflineSDK,calls,'offline')
                self.assertTrue(row['evaluation']['execution_contract_passed'],row)
                self.assertEqual(row['provider_calls'],0)
                if policy=='one_plan':self.assertEqual(row['mock_transport_calls'],1)
                if policy=='deterministic':self.assertEqual(row['mock_transport_calls'],0)

    def test_deterministic_baseline_retries_instead_of_being_artificially_weakened(self):
        row=run_row(self.store,CASES[1],'deterministic',OfflineSDK,Calls(),'offline')
        self.assertEqual(row['status'],'complete');self.assertEqual(row['tool_actions'],5)
        self.assertTrue(row['evaluation']['checks']['observed_empty_then_retry'])

    def test_partial_and_premature_reports_do_not_count_as_complete_contract(self):
        task=prepare(self.store,'direct');task['status']='partial';task['report']={'study_evidence':[],'gaps':['Not done']}
        self.assertFalse(score(task,CASES[0])['execution_contract_passed'])
        row=run_row(self.store,CASES[2],'deterministic',OfflineSDK,Calls(),'offline')
        self.assertEqual(row['status'],'partial');self.assertTrue(row['evaluation']['execution_contract_passed'])

    def test_call_budget_is_reserved_even_when_provider_raises(self):
        class BrokenSDK:
            def invoke(self,messages):raise TimeoutError()
        calls=Calls(limit=1);sdk=MeteredSDK(BrokenSDK(),calls)
        with self.assertRaises(TimeoutError):sdk.invoke([])
        with self.assertRaises(RuntimeError):sdk.invoke([])
        self.assertEqual(calls.used,1);self.assertEqual(sdk.count,1)

    def test_invalid_policy_extra_fields_do_not_reach_executor(self):
        from langchain_core.messages import AIMessage
        class InvalidSDK(OfflineSDK):
            def invoke(self,messages):return AIMessage(content='',tool_calls=[{'name':'submit_execution_policy','args':{'retry_empty':True,'inspect_returned':True,'source_id':'forged'},'id':'x','type':'tool_call'}])
        row=run_row(self.store,CASES[0],'one_plan',InvalidSDK,Calls(),'offline')
        self.assertEqual(row['status'],'failed');self.assertEqual(row['tool_actions'],0)
        self.assertFalse(row['evaluation']['execution_contract_passed'])

    def test_recorded_reply_contains_structured_action_but_not_free_thought(self):
        from langchain_core.messages import AIMessage
        class WithText(OfflineSDK):
            def invoke(self,messages):
                result=super().invoke(messages);result.content='DO NOT STORE FREE THOUGHT';return result
        sdk=MeteredSDK(WithText(),Calls());plan_once(sdk,prepare(self.store,'direct'))
        self.assertNotIn('DO NOT STORE',str(sdk.responses));self.assertTrue(sdk.responses[0]['tool_calls'])

    def test_existing_output_rejected_before_loading_provider_settings(self):
        with patch('sys.argv',['benchmark','--live-synthetic','--out',self.tmp.name]):
            with self.assertRaises(FileExistsError):main()


if __name__=='__main__':unittest.main()
