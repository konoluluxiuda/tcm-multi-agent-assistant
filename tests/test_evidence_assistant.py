"""Two model calls at most, grounded source IDs, bounded tools and safe failures."""
import json
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage
from tcm.evidence_assistant import EvidenceAssistant


class Model:
    def __init__(self,outputs):self.outputs=iter(outputs);self.calls=0
    def invoke(self,messages):
        self.calls+=1;value=next(self.outputs)
        if isinstance(value,Exception):raise value
        return AIMessage(content=json.dumps(value),usage_metadata={'input_tokens':10,'output_tokens':20,'total_tokens':30})


def local(q):
    return {'status':'candidates','hits':[{'name':'示例药','label':'功能与主治','text':'来源测试文字。还有未被选作摘录的完整原文。','chunk_id':'chunk-fixture','scope':'monograph'}]}


def answer(ids=None,group='traditional',quote='来源测试文字'):
    identifiers=ids or ['L1']
    return {'claims':[{'text':'来源中的资料记载。','source_group':group,'evidence_ids':identifiers,
                      'supports':[{'evidence_id':i,'field':'text','quote':quote} for i in identifiers]}],'limitations':[]}


class WorkflowTests(unittest.TestCase):
    def test_synthetic_mode_never_uses_real_source_runtimes(self):
        from tcm.evidence_assistant import configured_assistant
        with patch.dict('os.environ',{'EVIDENCE_ASSISTANT_DATA_MODE':'synthetic'}), \
             patch('tcm.agent_research.runtime.get_service',side_effect=AssertionError('real corpus read')), \
             patch('tcm.agent_pubmed.runtime.execute',side_effect=AssertionError('real literature fetched')):
            flow=configured_assistant()
            flow.model=Model([{'local_queries':['示例草的功效'],'pubmed_query':'Example Herb'},answer(quote='测试属性A')])
            result=flow.run('整理虚构示例草及合成文献')
            self.assertEqual(result['status'],'ok')
            self.assertEqual(result['data_mode'],'synthetic_only')
            self.assertIn('合成资料测试',result['message'])
            self.assertEqual(result['evidence'][1]['pmid'],'synthetic:paper-1')

    def run_flow(self,outputs,tool=local):
        model=Model(outputs);flow=EvidenceAssistant(model,local_tool=tool)
        return flow.run('根据药典整理示例药资料'),model

    def test_two_call_chain_has_usage_trace_and_resolvable_sources(self):
        r,m=self.run_flow([{'local_queries':['示例药的功效']},answer()])
        self.assertEqual(r['status'],'ok');self.assertEqual(m.calls,2)
        self.assertEqual((r['input_tokens'],r['output_tokens']),(20,40));self.assertIsNone(r['cost'])
        self.assertIn('[L1]',r['message']);self.assertEqual(r['evidence'][0]['chunk_id'],'chunk-fixture')
        self.assertEqual([s['phase'] for s in r['trace']],['plan','retrieve_local','generate'])

    def test_invalid_plan_or_timeout_does_not_call_a_tool_or_retry(self):
        def forbidden(q):raise AssertionError('tool should not be called')
        for output in ({'sql':'DROP TABLE'},TimeoutError('private provider credential')):
            r,m=self.run_flow([output],forbidden)
            self.assertEqual(r['status'],'planning_failed');self.assertEqual(m.calls,1)
            self.assertNotIn('private provider',str(r))

    def test_clarification_and_empty_evidence_stop_after_one_call(self):
        r,m=self.run_flow([{'clarification':'请明确药名。'}]);self.assertEqual(r['status'],'clarification_needed');self.assertEqual(m.calls,1)
        r,m=self.run_flow([{'local_queries':['未知药的功效']}],lambda q:{'status':'name_not_resolved','hits':[]})
        self.assertEqual(r['status'],'no_evidence');self.assertEqual(m.calls,1)

    def test_invented_or_wrong_group_citations_are_not_exposed_as_answers(self):
        for a in (answer(['P999']),answer(['L1'],'literature')):
            r,m=self.run_flow([{'local_queries':['示例药的功效']},a])
            self.assertEqual(r['status'],'invalid_citations');self.assertIsNone(r['answer'])
            self.assertNotIn('来源中的资料记载',r['message'])

    def test_tool_budget_rejects_three_local_queries(self):
        r,m=self.run_flow([{'local_queries':['a','b','c']}]);self.assertEqual(r['status'],'planning_failed');self.assertEqual(m.calls,1)

    def test_partial_tool_failure_keeps_available_evidence_separate(self):
        flow=EvidenceAssistant(Model([{'local_queries':['示例药的功效'],'pubmed_query':'fixture'},answer()]),local_tool=local,
            literature_tool=lambda q:{'status':'error','articles':[]})
        r=flow.run('整理资料和文献');self.assertEqual(r['status'],'partial')
        self.assertEqual(r['tool_statuses'][1]['status'],'error');self.assertIn('部分工具',r['message'])

    def test_retracted_sources_do_not_enter_generated_conclusions(self):
        paper={'pmid':'123','title':'fixture','abstract':[],'assessment':{'flags':['retracted']}}
        flow=EvidenceAssistant(Model([{'pubmed_query':'fixture'}]),literature_tool=lambda q:{'status':'ok','articles':[paper]})
        self.assertEqual(flow.run('检索研究')['status'],'no_evidence')

    def test_excluded_incomplete_and_retracted_sources_mark_partial_with_reason(self):
        from tcm.assistant_fixtures import boundary_tools
        for profile in ('incomplete','retracted'):
            tool,papers=boundary_tools(profile)
            plan={'local_queries':['示例草的功效和注意事项']}
            if profile=='retracted':plan['pubmed_query']='Example Herb'
            flow=EvidenceAssistant(Model([plan,answer(quote='测试属性A')]),local_tool=tool,literature_tool=papers)
            result=flow.run('整理示例草资料')
            self.assertEqual(result['status'],'partial')
            rejected=[t for t in result['tool_statuses'] if t['excluded_sources']]
            self.assertEqual(len(rejected),1)
            self.assertEqual(rejected[0]['status'],'partial_evidence')
            self.assertIn(rejected[0]['provider_status'],('candidates','ok'))
            self.assertEqual(rejected[0]['exclusion_reason'],'incomplete_field' if profile=='incomplete' else 'withdrawn_or_flagged')
            expected=['synthetic-local-efficacy'] if profile=='incomplete' else ['synthetic-local-efficacy','synthetic-local-caution']
            self.assertEqual([e['chunk_id'] for e in result['evidence']],expected)

    def test_empty_successful_tool_and_malformed_hits_do_not_imply_complete_evidence(self):
        for paper_result in ({'status':'ok','articles':[]}, {'status':'ok','articles':[{'assessment':{}}]}):
            flow=EvidenceAssistant(Model([{'local_queries':['示例药的功效'],'pubmed_query':'fixture'},answer()]),
                local_tool=local,literature_tool=lambda q:paper_result)
            result=flow.run('整理资料和文献')
            self.assertEqual(result['status'],'partial')
            self.assertIn(result['tool_statuses'][1]['status'],('no_usable_evidence','error'))
            self.assertEqual(result['tool_statuses'][1]['provider_status'],'ok')


class APITests(unittest.TestCase):
    def test_opt_in_before_memory_and_counts_bypass_models(self):
        import app
        from fastapi.testclient import TestClient
        with TestClient(app.app) as client, patch.dict('os.environ',{'ENABLE_EVIDENCE_ASSISTANT':'0'}), \
             patch.object(app,'process_query',side_effect=AssertionError('model reached')), \
             patch.object(app,'session_cookie',side_effect=AssertionError('memory reached')):
            self.assertEqual(client.post('/chat',json={'query':'整理资料','answer_mode':'evidence'}).status_code,503)
        with TestClient(app.app) as client, patch.dict('os.environ',{'ENABLE_EVIDENCE_ASSISTANT':'1','ENABLE_NATURAL_ROUTING':'1'}), \
             patch.object(app,'process_query',return_value={'messages':[AIMessage(content='统计')],'agent_name':'fixture'}) as process:
            r=client.post('/chat',json={'query':'有多少药材','answer_mode':'evidence'})
            self.assertEqual(r.status_code,200);self.assertNotIn('task_mode',process.call_args.kwargs)

    def test_real_main_graph_uses_child_workflow_and_saves_only_answer_not_source_text(self):
        import app
        from fastapi.testclient import TestClient
        from agents import agent_decision as ad
        flow=EvidenceAssistant(Model([{'local_queries':['示例药的功效']},answer()]),local_tool=local)
        with TestClient(app.app) as client, patch.dict('os.environ',{'ENABLE_EVIDENCE_ASSISTANT':'1','RESEARCH_TOOLS_ONLY':'1'}), \
             patch('tcm.evidence_assistant.EvidenceAssistant',return_value=flow), \
             patch.object(ad.LocalGuardrails,'check_input',side_effect=AssertionError('legacy guard called')), \
             patch.object(ad.LocalGuardrails,'check_output',side_effect=AssertionError('legacy guard called')):
            response=client.post('/chat',json={'query':'请整理示例药的资料','answer_mode':'evidence'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['evidence_answer']['model_calls'],2)
            self.assertEqual(response.json()['agent'],'EVIDENCE_ASSISTANT')
            history=client.get('/conversation').json()
            self.assertNotIn('来源测试文字。还有未被选作摘录的完整原文。',str(history['messages']))
            self.assertIn('来源测试文字',history['messages'][-1]['content'])
            self.assertIn('[L1]',history['messages'][-1]['content'])
