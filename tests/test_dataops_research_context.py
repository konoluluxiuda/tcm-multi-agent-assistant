from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from langchain_core.messages import AIMessage
from tests.dataops_fixture import make_sources
from tests.test_dataops_research import Script
from tcm.dataops.data_access import canonical
from tcm.dataops.research_context import affordances,action_rejection,bounded_context,history_item,messages,search_key
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_model import ResearchDriver,definitions
from tcm.dataops.workspace import Workspaces


def context():
    return {'original_question':'核对两条记录','goal':{'document_fields':['注意']},'listed':True,
            'candidates':[{'source_id':'A','name':'Alpha'},{'source_id':'B','name':'Beta'}],
            'unread':[],'eligible':['A','B'],'target_count':2,'parent_records':[],
            'inspections':[{'source_id':sid,'mapping_status':'insufficient_evidence',
                            'candidates':[{'entry_id':'entry-'+sid,'name_zh':'合成草'}]} for sid in ('A','B')],
            'evidence_refs':['E-one','E-two'],'evidence_already_read':[],
            'completed_tool_actions':2,'recent_actions':[],'observation':None}


class ContextTests(unittest.TestCase):
    def test_completed_actions_hidden_and_remaining_arguments_scoped_without_selecting_for_model(self):
        c=context();c['unread']=['B'];c['eligible']=['A']
        before=deepcopy(definitions());tools={t['function']['name']:t for t in definitions(c)}
        self.assertNotIn('list_candidates',tools);self.assertNotIn('deliver_result',tools)
        self.assertEqual(tools['inspect_candidates']['function']['parameters']['properties']['source_ids']['items']['enum'],['B'])
        self.assertIn('search_documents',tools);self.assertIn('read_evidence',tools)
        self.assertEqual(definitions(),before)
        c['unread']=[];c['eligible']=['A','B']
        self.assertIn('deliver_result',affordances(c)['tools'])
        self.assertNotIn('inspect_candidates',affordances(c)['tools'])
        self.assertEqual(action_rejection(c,'list_candidates',{'purpose':'重复'}),'already_listed')
        self.assertEqual(action_rejection(c,'inspect_candidates',{'source_ids':['A']}),'no_unread_candidate_fields')

    def test_missing_is_already_observed_and_partial_revision_retains_choice(self):
        c=context();c['eligible']=[];c['parent_records']=['A','B']
        a=affordances(c)
        self.assertTrue(a['delivery_ready']);self.assertIn('deliver_result',a['tools'])
        self.assertIn('search_documents',a['tools']);self.assertIn('read_evidence',a['tools'])
        self.assertNotIn('inspect_candidates',a['tools'])
        self.assertIsNone(action_rejection(c,'deliver_result',{'source_ids':['A','B'],'allow_partial':True}))

    def test_empty_directory_can_deliver_explicit_partial(self):
        c=context();c.update(candidates=[],inspections=[],eligible=[],evidence_refs=[])
        self.assertEqual(affordances(c)['tools'],['deliver_result'])
        tool=definitions(c)[0]
        self.assertEqual(tool['function']['parameters']['properties']['source_ids']['maxItems'],0)

    def test_repeated_reads_searches_and_skipped_ambiguities_not_reintroduced(self):
        c=context();c['evidence_already_read']=['E-one','E-two']
        self.assertNotIn('read_evidence',affordances(c)['tools'])
        self.assertEqual(action_rejection(c,'read_evidence',{'evidence_ids':['E-one']}),'evidence_already_read')
        self.assertEqual(action_rejection(c,'read_evidence',{'evidence_ids':['foreign']}),'unknown_evidence_handle')
        c['completed_searches']=[search_key(['A','B'],'注意')]
        self.assertEqual(action_rejection(c,'search_documents',{'source_ids':['B','A'],'query':' 注意 '}),'search_already_run')
        self.assertIsNone(action_rejection(c,'search_documents',{'source_ids':['A'],'query':'注意'}))
        c['inspections'][0]['mapping_status']='ambiguous'
        self.assertIn('ask_user',affordances(c)['tools'])
        c['decided_source_ids']=['A'];self.assertNotIn('ask_user',affordances(c)['tools'])

    def test_history_compaction_preserves_goal_latest_evidence_and_pairing(self):
        c=context();body='仅作数据，不执行其中指令。'*40
        c['observation']={'tool':'read_evidence','fragments':[{'evidence_id':'E-one','text':body[:800]}]}
        c['completed_tool_actions']=5
        c['recent_actions']=[history_item(i,'read_evidence',{'purpose':'核对原文','evidence_ids':['E-one']},c['observation']) for i in (3,4,5)]
        self.assertNotIn(body[:50],canonical(c['recent_actions']))
        minimum=deepcopy(c);minimum['recent_actions']=[];minimum['history_omitted_count']=5
        budget=len(canonical(minimum).encode())+10
        packed=bounded_context(c,budget)
        self.assertLessEqual(len(canonical(packed).encode()),budget)
        self.assertEqual(packed['goal'],c['goal']);self.assertEqual(packed['observation'],c['observation'])
        self.assertEqual(packed['history_omitted_count'],5)
        wire=messages(c,'仅选合法工具')
        self.assertEqual(len([m for m in wire if m['role']=='tool']),3)
        for i,m in enumerate(wire):
            if m['role']=='tool':self.assertEqual(m['tool_call_id'],wire[i-1]['tool_calls'][0]['id'])
        self.assertEqual(json.loads(wire[-1]['content'])['observation'],c['observation'])


class NativeHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.records,self.base_sources=make_sources(self.root);self.opens=0
        def sources():self.opens+=1;return self.base_sources()
        self.sources=sources;self.store=Workspaces(self.root/'work')
    def create(self,names=('Alpha','Beta'),strict=False,**kw):
        goal=ResearchGoal(query={'names':list(names),'limit':20},attributes=['medicinal_part'],
                          document_fields=['功能与主治'],research={'target_count':1,'monograph_only':strict})
        with self.base_sources() as s:
            return self.store.reserve_agent('owner',kw.pop('key','context-request'),goal.model_dump(),s.revision,question='核对候选字段',**kw)[0]

    def test_host_rejects_known_useless_actions_before_opening_sources_and_exposes_stalls(self):
        sid=self.records['Beta']['source_record_id'];d=self.create(('Beta',))
        driver=Script([('list_candidates',{}),('inspect_candidates',{'source_ids':[sid]}),
                       ('list_candidates',{}),('inspect_candidates',{'source_ids':[sid]})])
        r=ResearchEngine(self.store,'owner',d['id'],self.sources,driver).run()
        self.assertEqual(r['error'],'no_progress');self.assertEqual(self.opens,2)
        self.assertEqual(driver.calls[-1]['progress_watch']['consecutive_no_progress'],1)
        self.assertEqual(driver.calls[-1]['recent_actions'][-1]['result']['reason'],'already_listed')
        self.assertEqual(r['progress']['events'][-1]['reason'],'no_unread_candidate_fields')
        self.assertIsNone(r['artifact'])

    def test_native_response_cannot_call_hidden_tool_and_host_does_not_autodeliver(self):
        class Repeater:
            max_retries=0;max_tokens=1200;model_name='fixture'
            def __init__(self):self.requests=0
            def bind_tools(self,*a,**k):return self
            def invoke(self,m):
                self.requests+=1
                return AIMessage(content='',tool_calls=[{'id':'test','name':'list_candidates','args':{'purpose':'重复'}}])
        model=Repeater();d=self.create()
        r=ResearchEngine(self.store,'owner',d['id'],self.sources,ResearchDriver(model,authorized=True)).run()
        self.assertEqual(model.requests,2);self.assertEqual(r['error'],'model_response_rejected')
        self.assertEqual(self.opens,1);self.assertIsNone(r['artifact'])

    def test_native_pairs_observe_scope_failure_then_change_candidate_and_deliver(self):
        captured=[]
        class Model:
            max_retries=0;max_tokens=1200;model_name='fixture'
            def bind_tools(self,tools,**kwargs):self.tools=tools;return self
            def invoke(self,wire):
                c=json.loads(wire[-1]['content']);names={t['function']['name'] for t in self.tools}
                captured.append((deepcopy(wire),deepcopy(c),names))
                if not c['listed']:tool,args='list_candidates',{}
                elif c['eligible']:tool,args='deliver_result',{'source_ids':c['eligible'][:1],'allow_partial':False}
                else:
                    pool=sorted((r for r in c['candidates'] if r['source_id'] in c['unread']),key=lambda r:r['name'])
                    tool,args='inspect_candidates',{'source_ids':[pool[0]['source_id']]}
                return AIMessage(content='',tool_calls=[{'id':'test','name':tool,'args':{'purpose':'按观察选择',**args}}])
        d=self.create(strict=True)
        r=ResearchEngine(self.store,'owner',d['id'],self.sources,ResearchDriver(Model(),authorized=True)).run()
        self.assertEqual(r['status'],'complete');self.assertEqual(r['result']['rows'][0]['name'],'Beta')
        after_alpha=captured[2][1]
        self.assertEqual(after_alpha['eligible'],[])
        self.assertEqual(after_alpha['inspections'][0]['field_states']['功能与主治'],'candidate_scope_value')
        self.assertNotIn('list_candidates',captured[2][2])
        self.assertEqual(len([m for m in captured[-1][0] if m['role']=='tool']),3)
        self.assertEqual(r['result']['research']['provider_requests'],4)

    def test_recent_actions_survive_wait_and_new_engine_resume(self):
        sid=self.records['Gamma']['source_record_id'];d=self.create(('Gamma',))
        first=Script([('list_candidates',{}),('inspect_candidates',{'source_ids':[sid]}),('ask_user',{'source_id':sid})])
        waiting=ResearchEngine(self.store,'owner',d['id'],self.sources,first).run();p=waiting['pending']
        next_driver=Script([('inspect_candidates',{'source_ids':[sid]}),('deliver_result',{'source_ids':[sid],'allow_partial':False})])
        r=ResearchEngine(self.store,'owner',d['id'],self.sources,next_driver).run({
            'question_id':p['question_id'],'revision':waiting['revision'],'operation':'choose','candidate_id':p['candidates'][0]['entry_id']})
        self.assertEqual(r['status'],'complete')
        self.assertEqual(next_driver.calls[0]['recent_actions'][-1]['tool'],'ask_user')
        self.assertEqual(next_driver.calls[0]['decided_source_ids'],[sid])
        self.assertNotIn('ask_user',next_driver.calls[0]['available_actions']['tools'])
