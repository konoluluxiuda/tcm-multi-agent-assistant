import unittest
from types import SimpleNamespace
from scripts.eval_intent_contract_live import score, Recorder


class IntentEvaluationTests(unittest.TestCase):
    def test_correct_action_does_not_hide_dropped_fields_or_priority(self):
        case={'question':'示例叶先看性状，也要功效','expected':{
            'action':'research','entities':['示例叶'],'fields':['性状','功能与主治'],'priority_field':'性状'}}
        proposal={'action':'research','entities':['示例叶'],'fields':['功能与主治']}
        result=score(case,{'intent_status':'ok'},proposal)
        self.assertFalse(result['passed']);self.assertIn('fields',result['failure'])
        self.assertIn('priority_field',result['failure'])

    def test_provider_failure_is_not_correct_clarification(self):
        case={'question':'这个怎么用','expected':{'action':'clarify'}}
        self.assertFalse(score(case,{'intent_status':'unavailable'},None)['passed'])
        self.assertTrue(score(case,{'intent_status':'ok'},{'action':'clarify'})['passed'])

    def test_recorder_blocks_second_invoke_and_only_records_token_counters(self):
        class SDK:
            def bind_tools(self,*args,**kwargs):return self
            def invoke(self,messages):return SimpleNamespace(tool_calls=[{'name':'submit_query_intent','args':{'action':'clarify'}}],
                usage_metadata={'input_tokens':2,'output_tokens':3,'total_tokens':5,'private':'not recorded'})
        r=Recorder(SDK()).bind_tools([]);r.invoke([])
        self.assertEqual(r.usage,{'input_tokens':2,'output_tokens':3,'total_tokens':5})
        with self.assertRaises(RuntimeError):r.invoke([])


if __name__=='__main__':unittest.main()
