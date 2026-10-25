"""Offline native-tool response contracts; no provider or source data leaves process."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from tcm.intent_planner import plan_question
from tcm.agent_research import ResearchRuntime
from tests.test_intent_planner import Model
from tests.test_mineru_chat import Service
from tests.test_mineru_retrieval import fixture
from scripts.build_mineru_retrieval import build


class FieldContractTests(unittest.TestCase):
    def test_priority_is_grounded_and_original_is_kept(self):
        q='示例叶的功效和性状都整理一下，把外观放最前'
        model=Model({'action':'research','entities':['示例叶'],'fields':['功能与主治','性状'],
                     'priority_field':'性状','priority_evidence':'把外观放最前'})
        routed=plan_question(q,sdk=model)
        self.assertEqual(routed['intent_status'],'ok')
        self.assertEqual(routed['request']['original_question'],q)
        self.assertEqual(routed['request']['question'],'示例叶的功能与主治与性状，先列性状')
        self.assertEqual(len(model.inputs),1)
        self.assertEqual(model.inputs[0][1].content,q)
        self.assertEqual(len(model.inputs[0]),2)
        self.assertNotIn('evidence',model.binds[0][0][0][0]['function']['parameters']['properties'].get('fields',{}))

    def test_invalid_or_unresolved_requirements_do_not_query(self):
        base={'action':'research','entities':['示例叶'],'fields':['性状']}
        for extra in ({'priority_field':'注意','priority_evidence':'先看注意'},
                      {'priority_field':'性状'},
                      {'priority_field':'性状','priority_evidence':'凭空添加的要求'},
                      {'unresolved_requirements':['还要查文献']},
                      {'priority_field':'性状','priority_evidence':'先看性状','action':'herb_profile'}):
            result=plan_question('示例叶先看性状',sdk=Model({**base,**extra}))
            self.assertEqual(result['intent_status'],'invalid_output')
            self.assertEqual(result['tool'],'assistant')
        result=plan_question('资料和其他未知要求',sdk=Model({'action':'clarify','unresolved_requirements':['其他未知要求']}))
        self.assertEqual(result['tool'],'assistant');self.assertEqual(result['intent_status'],'ok')

    def test_local_tool_execution_and_source_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root/'source');build(root/'source',root/'index')
            service=Service(root/'index');runtime=ResearchRuntime()
            q='请把示例叶能够派上用场的记载给我'
            route=plan_question(q,sdk=Model({'action':'research','entities':['示例叶'],'fields':['功能与主治']}))
            with patch.object(runtime,'get_service',return_value=service):
                result,ctx=runtime.execute(route['request'],None)
                self.assertEqual(result['status'],'candidates')
                self.assertEqual(result['intent_request']['original_question'],q)
                self.assertTrue(result['tool_result']['hits'][0]['evidence'])
                replay,_=runtime.execute({'question':'出处呢'},ctx)
                self.assertEqual(replay['tool_result']['hits'],result['tool_result']['hits'])

    def test_timeout_never_retries(self):
        model=Model(error=TimeoutError('private provider information'))
        result=plan_question('示例叶怎样放才妥当',sdk=model)
        self.assertEqual(len(model.inputs),1)
        self.assertEqual(result['intent_status'],'unavailable')
        self.assertNotIn('private provider',str(result))


if __name__=='__main__':unittest.main()
