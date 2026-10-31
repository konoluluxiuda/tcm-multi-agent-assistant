from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from langchain_core.messages import AIMessage

from tests.dataops_fixture import make_sources
from tcm.dataops.research_question import bounded_catalog,compile_draft,local_proposal,request_proposal,draft_context,QuestionNeedsInput
from tcm.dataops.research_contracts import ResearchGoal,same_research_scope
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_model import ResearchSynthetic
from tcm.dataops.workspace import Workspaces
from tcm.dataops.artifacts import verify


def draft(**kw):
    return {'status':'ready','operation':'new','mode':'audit_named','names':['Alpha','Beta'],'filters':[],
            'target_count':None,'attributes':[],'document_fields':['功能与主治','注意'],
            'monograph_only':None,'issues':[],'unresolved':[],**kw}


class QuestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.records,self.sources=make_sources(self.root);self.store=Workspaces(self.root/'work')
        with self.sources() as s:self.catalog=s.name_catalog()
    def catalog_for(self,q):return bounded_catalog(q,self.catalog)
    def reserve(self,goal,key='question-one',**kw):
        with self.sources() as s:
            return self.store.reserve_agent('owner',key,goal,s.revision,question='保留原始问句',**kw)[0]
    def run_doc(self,doc):return ResearchEngine(self.store,'owner',doc['id'],self.sources,ResearchSynthetic()).run()

    def test_named_audit_retains_missing_rows_and_distinguishes_completeness(self):
        q='整理Alpha和Beta的功效和注意，保留出处并生成表格'
        p=local_proposal(q,self.catalog_for(q));self.assertEqual(p['status'],'ready',p)
        self.assertEqual(p['goal']['research']['mode'],'audit_named');self.assertEqual(p['goal']['attributes'],['medicinal_part'])
        doc=self.run_doc(self.reserve(p['goal']))
        self.assertEqual(doc['status'],'complete');self.assertEqual(len(doc['result']['rows']),2)
        self.assertEqual(doc['result']['research']['qualified_count'],0)
        self.assertTrue(all(r['cells']['注意']['status']=='missing' for r in doc['result']['rows']))
        stored=self.store.get('owner',doc['id']);self.assertEqual(verify(self.store.root/doc['id']/doc['artifact']['filename'],stored['result'])['status'],'passed')

    def test_candidate_limit_is_not_target_count(self):
        q='从Alpha和Beta中挑一条功效，保留出处并生成表格'
        p=local_proposal(q,self.catalog_for(q));self.assertEqual(p['status'],'ready',p)
        self.assertEqual(p['goal']['query']['limit'],20);self.assertEqual(p['goal']['research']['target_count'],1)
        self.assertEqual(p['goal']['research']['mode'],'select_complete')

    def test_append_preserves_parent_scope_old_fields_and_old_artifact(self):
        q='整理Alpha和Beta的功效，生成表格';first=local_proposal(q,self.catalog_for(q))
        parent=self.run_doc(self.reserve(first['goal']));before=(self.store.root/parent['id']/parent['artifact']['filename']).read_bytes()
        question='增加贮藏，保留旧字段';p=local_proposal(question,{},parent=parent)
        self.assertEqual(p['status'],'ready',p);self.assertEqual(p['goal']['query'],parent['goal']['query'])
        self.assertEqual(p['goal']['document_fields'],['功能与主治','贮藏'])
        child=self.run_doc(self.reserve(p['goal'],key='question-v2',parent_id=parent['id'],expected_revision=1))
        self.assertEqual(child['status'],'complete');self.assertEqual({r['source_id'] for r in child['result']['rows']},{r['source_id'] for r in parent['result']['rows']})
        self.assertEqual((self.store.root/parent['id']/parent['artifact']['filename']).read_bytes(),before)

    def test_unknown_and_out_of_scope_whole_request_is_not_partially_executed(self):
        for q in ('整理它们的功效','整理Alpha功效并预测疗效','Alpha的来源做成表','Alpha可能是Beta，整理功效'):
            p=local_proposal(q,self.catalog_for(q));self.assertEqual(p['status'],'needs_input',(q,p));self.assertIsNone(p['goal'])

    def test_known_objects_cannot_be_dropped_or_invented_by_model(self):
        q='整理Alpha和Beta的功效'
        for names in (['Alpha'],['Alpha','Unknown']):
            with self.assertRaises(ValueError):compile_draft(draft(names=names),q,self.catalog_for(q))

    def test_provenance_is_not_botanical_origin_and_explicit_fields_do_not_add_defaults(self):
        q='整理Alpha和Beta的功效，保留出处'
        p=compile_draft(draft(document_fields=['功能与主治']),q,self.catalog_for(q))
        self.assertEqual(p['goal']['document_fields'],['功能与主治'])
        p=compile_draft(draft(document_fields=['功能与主治','来源']),q,self.catalog_for(q))
        self.assertEqual(p['status'],'needs_input')

    def test_parent_values_history_and_paths_never_enter_model_payload(self):
        q='整理Alpha和Beta功效';p=local_proposal(q,self.catalog_for(q));parent=self.run_doc(self.reserve(p['goal']))
        parent['result']['private']='must-not-send';parent['secret']='secret-path'
        payload=draft_context('增加贮藏',{},parent)
        self.assertNotIn('must-not-send',json.dumps(payload));self.assertNotIn('secret-path',json.dumps(payload))
        self.assertNotIn('source_id',json.dumps(payload));self.assertNotIn('evidence',json.dumps(payload))

    def test_legacy_selection_goal_mode_defaults_are_backward_compatible(self):
        old=ResearchGoal(query={'names':['Alpha']},research={'target_count':1}).model_dump()
        del old['research']['mode'];normalized=ResearchGoal.model_validate(old).model_dump()
        self.assertTrue(same_research_scope(old,normalized))

    def test_schema_valid_but_unrequested_or_negated_columns_cannot_execute(self):
        q='Alpha和Beta的功效做成表，别加注意和拉丁名，但保留出处。'
        for fields in ({'attributes':['properties','flavors','meridians','efficacy_class','indications'],
                        'document_fields':['功能与主治']},
                       {'attributes':['latin_name'],'document_fields':['功能与主治']},
                       {'document_fields':['功能与主治','注意']}):
            result=compile_draft(draft(**fields),q,self.catalog_for(q))
            self.assertEqual(result['status'],'needs_input');self.assertIsNone(result['goal'])
        q='整理Alpha和Beta的拉丁名、功效'
        self.assertEqual(compile_draft(draft(attributes=['latin_name'],document_fields=['功能与主治']),q,self.catalog_for(q))['status'],'ready')

    def test_parent_scope_is_host_bound_and_storage_is_not_habitat(self):
        q='整理Alpha和Beta功效';p=local_proposal(q,self.catalog_for(q));parent=self.run_doc(self.reserve(p['goal']))
        q='在上一份上再添一栏存放环境，原来的内容别删。'
        self.assertTrue(draft_context(q,{},parent)['parent']['objects_bound_by_host'])
        p=compile_draft(draft(operation='append_fields',names=[],attributes=['habitat'],document_fields=[]),q,{},parent=parent)
        self.assertEqual(p['status'],'needs_input');self.assertIsNone(p['goal'])
        p=compile_draft(draft(operation='append_fields',names=[],document_fields=['贮藏']),q,{},parent=parent)
        self.assertEqual(p['status'],'ready');self.assertEqual(p['goal']['document_fields'],['功能与主治','贮藏'])

    def test_preparation_and_compound_substrings_do_not_become_a_plain_herb(self):
        catalog={'当归':['etcm2-local:'+'a'*64],'川芎':['etcm2-local:'+'b'*64]}
        for q in ('整理酒当归和川芎的功效','当归苷的功效做成表','当归与酒当归的功效'):
            with self.assertRaises(QuestionNeedsInput):bounded_catalog(q,catalog)
        self.assertEqual(set(bounded_catalog('整理当归和川芎功效',catalog)),{'当归','川芎'})

    def test_native_one_call_bounded_and_guarded(self):
        outer=self
        class Model:
            max_retries=0;max_tokens=1200;model_name='fixture';calls=0
            def bind_tools(self,tools,**kw):
                outer.assertFalse(kw['parallel_tool_calls']);outer.assertEqual(tools[0]['function']['name'],'propose_research_goal');return self
            def invoke(self,messages):
                self.calls+=1
                return AIMessage(content='',tool_calls=[{'id':'g','name':'propose_research_goal','args':draft()}])
        m=Model();q='Alpha和Beta的资料'
        with self.assertRaises(PermissionError):request_proposal(m,q,self.catalog_for(q))
        r=request_proposal(m,q,self.catalog_for(q),authorized=True)
        self.assertEqual(m.calls,1);self.assertEqual(r['external_model_calls'],1)
        r=request_proposal(m,'Alpha功效并预测疗效',self.catalog_for('Alpha'),authorized=True)
        self.assertEqual(m.calls,1);self.assertEqual(r['external_model_calls'],0)

    def test_missing_explicit_columns_and_conflicting_selection_are_not_executable(self):
        cases=[('整理Alpha和Beta的功效、贮藏，生成表格',draft(document_fields=['功能与主治'])),
               ('整理Alpha和Beta的拉丁名、功效',draft(document_fields=['功能与主治'])),
               ('整理Alpha和Beta的功效，生成表格',draft(mode='select_complete',target_count=1,document_fields=['功能与主治'])),
               ('从Alpha和Beta中挑两条功效齐全的',draft(mode='select_complete',target_count=1,document_fields=['功能与主治'])),
               ('从Alpha和Beta中挑一条功效齐全的，只接受原药材正文',draft(mode='select_complete',target_count=1,document_fields=['功能与主治']))]
        for question,args in cases:
            p=compile_draft(args,question,self.catalog_for(question))
            self.assertEqual(p['status'],'needs_input',(question,p));self.assertIsNone(p['goal'])

    def test_negation_clause_does_not_swallow_but_retained_positive_field(self):
        q='Alpha和Beta的功效做成表，不要增加注意，但保留贮藏和出处'
        p=compile_draft(draft(document_fields=['功能与主治','贮藏']),q,self.catalog_for(q))
        self.assertEqual(p['status'],'ready',p)
        p=compile_draft(draft(document_fields=['功能与主治']),q,self.catalog_for(q))
        self.assertEqual(p['status'],'needs_input')

    def test_parent_change_request_cannot_be_ignored_even_if_model_keeps_old_objects(self):
        q='整理Alpha和Beta功效';p=local_proposal(q,self.catalog_for(q));parent=self.run_doc(self.reserve(p['goal']))
        args=draft(operation='append_fields',names=[],document_fields=['贮藏'])
        for q in ('把对象换成Delta，再增加贮藏','增加贮藏，同时删除功效列'):
            p=compile_draft(args,q,self.catalog_for(q),parent=parent)
            self.assertEqual(p['status'],'needs_input');self.assertIsNone(p['goal'])
        p=compile_draft(args,'增加贮藏，不要删除旧列',{},parent=parent)
        self.assertEqual(p['status'],'ready',p)
