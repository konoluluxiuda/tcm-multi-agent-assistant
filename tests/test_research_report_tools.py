"""Unified report actions: real local workers, conservative completion and leaf budgets."""
import asyncio
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock,patch
from langchain_core.messages import AIMessage
from tests.research_report_fixture import ReportFixture,QUESTION
from tests.test_research_run_business import Sequence,call
from tcm.research_business_tools import CALL,SPECS,maximum_cost
from tcm.research_plan_intent import local_plan,validate_question
from tcm.research_run_scope import RunScope
from tcm.research_run import run,Limits
from tcm.research_run_local import LocalAnalysisTools,DeclaredActionsDriver,run_local_actions
from tcm.research_evidence_store import for_run
from tcm.research_run_durable import run_durable,RunJournal
from tcm.research_observation import validate
from tcm.research_evidence_read import read


class PlanTests(unittest.TestCase):
    def test_exact_pair_and_leaf_requests(self):
        plan=local_plan(QUESTION);self.assertEqual(len(plan.steps),1)
        args=plan.steps[0].call.arguments
        self.assertEqual(args.herbs,['Alpha','Beta']);self.assertTrue(args.all_targets)
        self.assertEqual(args.fields,['功能与主治','用法与用量']);self.assertEqual(args.attribute_fields,['medicinal_part'])
        mixed=local_plan('查询药典当归的功效，查询川芎的ETCM药用部位')
        self.assertEqual([s.call.tool for s in mixed.steps],['pharmacopoeia_lookup','herb_attributes'])
        self.assertIsNotNone(local_plan('比较当归与川芎的药典功效和用量并核对共同靶点'))

    def test_model_cannot_change_fields_all_scope_objects_or_source_ids(self):
        for mutate in ('fields','all','object','id','clauses'):
            p=local_plan(QUESTION).model_dump();step=p['steps'][0];args=step['call']['arguments']
            if mutate=='fields':args['fields']=['功能与主治']
            elif mutate=='all':args['all_targets']=False
            elif mutate=='object':args['herbs'][1]='Other'
            elif mutate=='id':args['source_ids']=['etcm2-local:'+'a'*64,'etcm2-local:'+'b'*64]
            else:step['clauses']=[0]
            with self.assertRaises(ValueError):validate_question(p,QUESTION)
        for suffix in ('，再查采购价格','，再查文献','，比较疗效','，查询第三味药'):
            self.assertIsNone(local_plan(QUESTION+suffix))

    def test_strict_scope_and_field_contracts(self):
        self.assertEqual(len(SPECS),8)
        raw=local_plan(QUESTION).steps[0].call.model_dump()
        with self.assertRaises(ValueError):RunScope.model_validate({'requirements':[{'id':'pair','call':raw}]})
        for fields in (['功能与主治','功能与主治'],['未知字段'],['注意']*5):
            with self.assertRaises(ValueError):CALL.validate_python({'tool':'pharmacopoeia_lookup','arguments':{'name':'Alpha','fields':fields}})


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=ReportFixture();self.f=self.fixture.__enter__();self.addCleanup(self.fixture.__exit__,None,None,None)
        self.scope=self.f.report_scope()
        self.backend=LocalAnalysisTools(None,herbs=self.f.herbs.base,enrichment=self.f.herbs.database,pharmacopoeia=self.f.pharma)
        self.evidence=for_run(self.f.root/'runs.sqlite3','owner','report-test-run')

    async def execute(self,scope=None,**kwargs):
        scope=scope or self.scope
        kwargs.setdefault('limits',Limits(tool_calls=16))
        return await run('Synthetic report',DeclaredActionsDriver(scope),self.backend,scope=scope,evidence_store=self.evidence,**kwargs)

    async def test_real_report_subgraph_counts_four_leaves_and_two_target_pages(self):
        paths=[self.f.herbs.base,self.f.herbs.database,self.f.pharma/'research.sqlite3',self.f.diseases]
        before={p:p.read_bytes() for p in paths}
        r=await self.execute();self.assertEqual(r['status'],'complete',r)
        self.assertEqual((r['model_calls'],r['tool_calls'],r['action_calls']),(2,6,1))
        data=self.evidence.resolve('obs-1',r['evidence']['obs-1'])['data']
        self.assertEqual(data['leaf_calls'],6);self.assertTrue(data['complete'])
        self.assertEqual(len(data['report']['targets']['items']),23)
        for item in data['report']['targets']['items']:self.assertEqual(len(item['sources']),2)
        self.assertTrue(all(p.read_bytes()==v for p,v in before.items()))

    async def test_partial_missing_field_retains_targets_but_never_completes(self):
        raw=self.scope.model_dump();raw['requirements'][0]['call']['arguments']['fields'].append('贮藏')
        r=await self.execute(RunScope.model_validate(raw));self.assertEqual(r['status'],'partial')
        data=self.evidence.resolve('obs-1',r['evidence']['obs-1'])['data']
        self.assertFalse(data['complete']);self.assertEqual(data['missing'],['Alpha：贮藏','Beta：贮藏'])
        self.assertEqual(len(data['report']['targets']['items']),23);self.assertFalse(r['coverage']['complete'])

    async def test_insufficient_worst_case_budget_rejects_before_any_dispatch(self):
        with patch.object(self.backend,'invoke_metered',new=AsyncMock()) as backend:
            r=await self.execute(limits=Limits(tool_calls=13))
            self.assertEqual(r['reason'],'tool_budget');self.assertEqual(r['tool_calls'],0);backend.assert_not_awaited()
        raw=self.scope.model_dump();raw['requirements'][0]['call']['arguments']['all_targets']=False
        r=await self.execute(RunScope.model_validate(raw),limits=Limits(tool_calls=5))
        self.assertEqual((r['status'],r['tool_calls']),('complete',5))

    async def test_wrong_pair_sources_do_not_leak_other_object_rows_or_query_targets(self):
        raw=self.scope.model_dump();raw['requirements'][0]['call']['arguments']['source_ids'].reverse()
        r=await self.execute(RunScope.model_validate(raw));self.assertEqual(r['status'],'partial')
        data=self.evidence.resolve('obs-1',r['evidence']['obs-1'])['data']
        self.assertEqual(r['tool_calls'],4);self.assertEqual(data['report']['targets']['status'],'unresolved_sources')
        self.assertTrue(all(not s['structured'].get('rows') for s in data['report']['source_reports']))

    async def test_leaf_pharma_and_attribute_actions_use_same_loop(self):
        scope=RunScope.model_validate({'requirements':[
            {'id':'text','call':{'tool':'pharmacopoeia_lookup','arguments':{'name':'Alpha','fields':['功能与主治']}}},
            {'id':'attr','call':{'tool':'herb_attributes','arguments':{'name':self.f.ids[1],'fields':['medicinal_part']}}}]})
        r=await self.execute(scope);self.assertEqual((r['status'],r['tool_calls'],r['action_calls']),('complete',2,2))
        for eid,payload in r['evidence'].items():
            self.assertEqual(read({eid:self.evidence.resolve(eid,payload)},{'evidence_id':eid,'field':'rows'})['status'],'ok')

    async def test_field_and_completion_forgery_are_rejected(self):
        r=await self.execute();raw=self.evidence.resolve('obs-1',r['evidence']['obs-1']);args=self.scope.requirements[0].call['arguments']
        for kind in ('field','missing','source','object'):
            bad=copy.deepcopy(raw)
            if kind=='field':bad['data']['request']['fields']=['注意']
            elif kind=='missing':bad['data']['missing']=['fake']
            elif kind=='source':bad['data']['report']['source_reports'][0]['structured']['source_record_id']=self.f.ids[1]
            else:bad['data']['report']['herbs']=['Alpha','Other']
            with self.assertRaises(ValueError):validate('pair_evidence_report',args,bad,{},max_bytes=4_000_000)

    async def test_wrapper_cannot_report_one_call_for_six_actual_reads(self):
        original=self.backend.invoke_metered
        async def forged(name,args,charge):
            result=await original(name,args,charge);result['data']['leaf_calls']=1;return result
        with patch.object(self.backend,'invoke_metered',new=forged):r=await self.execute()
        self.assertEqual(r['status'],'partial');self.assertEqual(r['tool_calls'],6)
        self.assertEqual(r['evidence']['obs-1']['status'],'error')

    async def test_saved_composite_resumes_without_replaying_any_leaf(self):
        class Crash(BaseException):pass
        async def stop(s):
            if s['last_node']=='execute':raise Crash()
        options=dict(path=self.f.root/'journal.sqlite3',owner='owner',identifier='composite-saved-001',
            contract='synthetic-report-v1',scope=self.scope,evidence_store=self.evidence,limits=Limits(tool_calls=16))
        with patch.object(self.backend,'invoke_metered',wraps=self.backend.invoke_metered) as backend:
            with self.assertRaises(Crash):await run_durable('q',DeclaredActionsDriver(self.scope),self.backend,on_state=stop,**options)
            r=await run_durable('q',DeclaredActionsDriver(self.scope),self.backend,resume=True,**options)
            self.assertEqual((r['status'],r['tool_calls']),('complete',6));self.assertEqual(backend.await_count,1)

    async def test_leaf_journal_failure_cannot_be_swallowed_by_report_fallbacks(self):
        original=RunJournal.save
        def fail(journal,state,phase,**kwargs):
            if phase=='pending' and state['tool_calls']:raise OSError('synthetic pre-dispatch write failure')
            return original(journal,state,phase,**kwargs)
        with patch.object(RunJournal,'save',fail),patch('tcm.cross_source_report.local_branch',new=AsyncMock()) as branch:
            with self.assertRaises(OSError):
                await run_durable('q',DeclaredActionsDriver(self.scope),self.backend,
                    path=self.f.root/'failure.sqlite3',owner='owner',identifier='composite-fail-001',
                    contract='synthetic-v1',scope=self.scope,evidence_store=self.evidence,limits=Limits(tool_calls=16))
            branch.assert_not_awaited()

    async def test_native_actions_share_budget_and_citation_checks(self):
        args=self.scope.requirements[0].call['arguments']
        sdk=Sequence([[call('pair_evidence_report',args,'report')],
            [call('read_run_evidence',{'evidence_id':'obs-1','field':'items','offset':20,'limit':3},'read')],
            [call('finish_research',{'status':'complete','evidence_ids':['obs-1'],'unresolved':[]},'done')]])
        r=await run('q',sdk,self.backend,scope=self.scope,evidence_store=self.evidence,limits=Limits(tool_calls=16))
        self.assertEqual((r['status'],r['tool_calls'],r['action_calls']),('complete',7,2))
        self.assertEqual(len(r['evidence']['obs-2']['data']['items']),3)

    async def test_parent_timeout_cancels_all_started_children(self):
        started=[];stopped=[]
        async def block(kind,herb,*a,**kw):
            key=(kind,herb);started.append(key)
            try:await asyncio.Future()
            finally:stopped.append(key)
        with patch('tcm.cross_source_report.local_branch',new=block):
            r=await self.execute(limits=Limits(call_seconds=.5,tool_calls=16))
        self.assertEqual(r['status'],'partial');self.assertTrue(started)
        self.assertCountEqual(started,stopped);self.assertEqual(r['tool_calls'],len(started))
