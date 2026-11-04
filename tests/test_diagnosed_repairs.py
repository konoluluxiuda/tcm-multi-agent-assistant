"""Repair contracts: full requirements, bounded all-match selection, real local reads."""
import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import sqlite3
import unittest

from tests.dataops_fixture import make_sources
from tests.research_business_fixture import BusinessFixture
from tests.test_research_question import draft
from tcm.intent_planner import grounded,QueryIntent,mapped
from tcm.relation_queries import parse
from tcm.research_plan_intent import local_plan,validate_question
from tcm.research_plan_run import compile_scope,format_result
from tcm.research_run_local import LocalAnalysisTools,DeclaredActionsDriver
from tcm.research_run import run
from tcm.research_report_tools import missing_for
from tcm.dataops.research_question import bounded_catalog,compile_draft,local_proposal,ResearchDraft
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_context import affordances
from tcm.dataops.research_model import ResearchFixed
from tcm.dataops.workspace import Workspaces
from tcm.dataops.artifacts import verify


class IntentRepairs(unittest.TestCase):
    def test_distributive_boundary_keeps_preparations_and_compounds_rejected(self):
        for q in ('山药和茯苓各有哪些成分和靶点','茯苓各自有哪些成分','茯苓分别有哪些成分','茯苓均有记录'):
            self.assertTrue(grounded('茯苓',q),q)
        for q in ('酒茯苓有哪些成分','茯苓提取物的成分','茯苓各向异性','假茯苓的资料','不查茯苓的靶点'):
            self.assertFalse(grounded('茯苓',q),q)

    def test_coordinated_relations_cannot_be_replaced_with_only_target_profiles(self):
        q='山药和茯苓各有哪些成分和靶点，来源写清楚'
        plan=local_plan(q);self.assertEqual(len(plan.steps),4)
        self.assertEqual({(s.call.arguments.name,s.call.arguments.kind) for s in plan.steps},
                         {(n,k) for n in ('山药','茯苓') for k in ('ingredient','target')})
        saved={'steps':[{'call':{'tool':'herb_target_profile','arguments':{'name':n}},'clauses':[0]} for n in ('山药','茯苓')]}
        with self.assertRaisesRegex(ValueError,'Relation requirements omitted'):validate_question(saved,q)
        for bad in (plan.model_dump()['steps'][:-1],plan.model_dump()['steps']+[plan.model_dump()['steps'][0]]):
            with self.assertRaises(ValueError):validate_question({'steps':bad},q)
        for suffix in ('，再预测新靶点','，再查价格','，只要第一味的结果','，查药典功效'):
            self.assertIsNone(local_plan(q+suffix))

    def test_target_only_uses_dedicated_relation_and_old_overbroad_model_report_still_rejects(self):
        for q in ('丹参和连翘有没有共同靶点，列出来并给出处','当归和川芎的共同靶点，保留双方原始记录的位置'):
            p=parse(q);self.assertEqual(p['kind'],'common_target',q)
        q='丹参和连翘有没有共同靶点，列出来并给出处'
        correct=mapped(QueryIntent(action='relation',entities=['丹参','连翘'],relation_kind='common_target'),q)
        self.assertEqual(correct['request']['kind'],'common_target')
        bad={'steps':[{'call':{'tool':'pair_evidence_report','arguments':{'herbs':['丹参','连翘'],
                      'fields':['功能与主治','注意'],'all_targets':True}},'clauses':[0]}]}
        with self.assertRaisesRegex(ValueError,'Uncovered pair'):validate_question(bad,q)
        self.assertIsNone(parse(q+'，再推荐治疗方案'))


class CompleteSelection(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.records,self.sources=make_sources(self.root)
        with self.sources() as s:self.catalog=s.name_catalog()

    def proposal(self,q):return local_proposal(q,bounded_catalog(q,self.catalog))

    def execute(self,goal):
        store=Workspaces(self.root/'work')
        with self.sources() as s:doc=store.reserve_agent('owner','all-complete-test',goal,s.revision,question='fixture')[0]
        result=ResearchEngine(store,'owner',doc['id'],self.sources,ResearchFixed()).run()
        self.assertEqual(verify(store.root/doc['id']/result['artifact']['filename'],result['result'])['status'],'passed')
        return result

    def test_all_named_candidates_checked_not_default_two_and_fields_unified(self):
        # Three complete records make an accidental default-two truncation visible.
        with sqlite3.connect(self.root/'enrichment.sqlite3') as con:
            con.execute('INSERT INTO name_candidates SELECT ?,entry_id,name_zh,evidence FROM name_candidates WHERE name_zh=?',
                        (self.records['Delta']['source_record_id'],'丁草'))
        q='看看Alpha、Beta、Gamma、Delta里，哪些药典功能和主治都有记录，做成表'
        p=self.proposal(q);self.assertEqual(p['status'],'ready',p)
        self.assertEqual(p['goal']['research'],{'mode':'filter_complete','target_count':4,'monograph_only':False})
        self.assertEqual(p['goal']['document_fields'],['功能与主治']);self.assertEqual(p['goal']['query']['filters'],[])
        result=self.execute(p['goal']);self.assertEqual(result['status'],'complete',result.get('error'))
        report=result['result']['research'];self.assertEqual(report['reviewed_count'],4)
        self.assertEqual(report['qualified_count'],3)
        self.assertEqual({r['source_id'] for r in result['result']['rows']},
                         {self.records[n]['source_record_id'] for n in ('Alpha','Beta','Delta')})
        self.assertTrue(report['all_matching_in_named_pool'])

    def test_zero_matches_is_completed_filter_with_reasons_not_unmet_n_target(self):
        p=self.proposal('看看Alpha、Beta里，哪些药典功效和注意都有记录，做成表')
        self.assertEqual(p['status'],'ready',p);result=self.execute(p['goal'])
        self.assertEqual(result['status'],'complete');self.assertEqual(result['result']['rows'],[])
        self.assertEqual(len(result['result']['research']['candidate_states']),2)
        self.assertTrue(all(x['reasons'] for x in result['result']['research']['candidate_states']))

    def test_saved_missing_filter_not_silently_corrected_and_mode_cannot_drop_selection(self):
        q='看看Alpha、Beta里，哪些药典功效都有记录，做成表';catalog=bounded_catalog(q,self.catalog)
        with self.assertRaises(ValueError):ResearchDraft.model_validate(draft(filters=[{'field':'功能与主治','op':'missing','value':''}]))
        p=compile_draft(draft(document_fields=['功能与主治']),q,catalog)
        self.assertEqual(p['status'],'needs_input');self.assertEqual(p['goal'],None)
        p=compile_draft(draft(mode='select_complete',target_count=1,document_fields=['功能与主治']),q,catalog)
        self.assertEqual(p['status'],'needs_input')
        correct=draft(mode='filter_complete',document_fields=['功能与主治'])
        self.assertEqual(compile_draft(correct,q,catalog)['status'],'ready')
        for extra in ('，推荐治疗方案','，计算相关性','，再查价格'):
            self.assertEqual(self.proposal(q+extra)['status'],'needs_input')

    def test_limit_and_unknown_extra_requirements_cannot_be_silently_discarded(self):
        from tcm.dataops.research_contracts import ResearchGoal
        p=self.proposal('看看Alpha、Beta里，哪些药典功效都有记录，做成表')
        bad=deepcopy(p['goal']);bad['query']['names']=['A','B','C','D','E','F']
        with self.assertRaises(ValueError):ResearchGoal.model_validate(bad)
        self.assertEqual(self.proposal('看看Alpha、Beta里，哪些药典功效都有记录，做成表，并计算相关性')['status'],'needs_input')

    def test_revision_retains_parent_rows_but_new_missing_fields_require_partial(self):
        p=self.proposal('看看Alpha、Beta里，哪些药典功效都有记录，做成表')
        store=Workspaces(self.root/'revision-work')
        with self.sources() as s:parent=store.reserve_agent('owner','parent-all-complete',p['goal'],s.revision,question='fixture')[0]
        parent=ResearchEngine(store,'owner',parent['id'],self.sources,ResearchFixed()).run()
        child=local_proposal('增加注意，保留旧字段',{},parent=parent)
        self.assertEqual(child['status'],'ready',child)
        with self.sources() as s:doc=store.reserve_agent('owner','child-all-complete',child['goal'],s.revision,
                                                       question='fixture',parent_id=parent['id'],expected_revision=1)[0]
        result=ResearchEngine(store,'owner',doc['id'],self.sources,ResearchFixed()).run()
        self.assertEqual(result['status'],'partial')
        self.assertEqual({r['source_id'] for r in result['result']['rows']},{r['source_id'] for r in parent['result']['rows']})
        self.assertTrue(all(r['cells']['注意']['status']=='missing' for r in result['result']['rows']))


class LocalRelationTools(unittest.IsolatedAsyncioTestCase):
    async def test_four_actual_reads_source_pages_and_forged_witness_rejection(self):
        with BusinessFixture() as f:
            question='Alpha和Beta各有哪些成分和靶点，来源写清楚'
            plan=local_plan(question);scope,unresolved=compile_scope(plan,{})
            self.assertIsNone(unresolved)
            backend=LocalAnalysisTools(None,herbs=f.herbs.base,enrichment=f.herbs.database)
            from tcm.research_evidence_store import for_run
            evidence=for_run(f.root/'runs.sqlite3','owner','repair-relations')
            result=await run(question,DeclaredActionsDriver(scope,batch_size=4,evidence_store=evidence),backend,scope=scope,evidence_store=evidence)
            self.assertEqual(result['status'],'complete',result.get('reason'))
            self.assertEqual(result['tool_calls'],4);self.assertEqual(len(result['evidence']),4)
            rendered=format_result(result,evidence)
            self.assertEqual(len(rendered['blocks']),4)
            self.assertTrue(all(b['next_page']==2 and len(b['field_rows'])==20 for b in rendered['blocks']))
            self.assertIn('成分',rendered['message']);self.assertIn('靶点',rendered['message'])
            eid,raw=next(iter(result['evidence'].items()));data=evidence.resolve(eid,raw)['data'];args=data['request']
            self.assertEqual(missing_for('herb_relation_records',args,data['report']),[])
            bad=deepcopy(data['report']);bad['relations']['data']['items'][0]['herb_id']='other'
            with self.assertRaisesRegex(ValueError,'provenance'):missing_for('herb_relation_records',args,bad)
            from tcm.research_report_tools import execute
            charges=[]
            from tcm.cross_source_report import local_branch
            async def branch(kind,herb,question,source_id=None):
                return await local_branch(kind,herb,question,source_record_id=source_id)
            second=await execute('herb_relation_records',{**args,'page':2},branch,lambda:charges.append(1),backend.contract)
            self.assertEqual(len(charges),1);self.assertEqual(second['data']['report']['relations']['data']['returned_count'],5)
