"""Exposed mode regression and adversarial host constraints; no model scoring."""
import unittest
import tempfile
from pathlib import Path

from tests.dataops_fixture import make_sources
from tests.test_research_question import draft
from tcm.dataops.research_question import compile_draft, mode_constraints, all_complete_request
from tcm.dataops.failures import public_failure


class ModeConstraintsTests(unittest.TestCase):
    catalog={'甘草':['a'],'陈皮':['b'],'大黄':['c']}

    def proposal(self,question,**changes):
        return compile_draft(draft(**{'names':list(self.catalog),'mode':'filter_complete',**changes}),question,self.catalog)

    def test_saved_model_filter_survives_unknown_local_completeness_phrase(self):
        q='甘草、陈皮、大黄中，哪些同时有药典功效和注意记载？只把符合条件的整理成表。'
        self.assertFalse(all_complete_request(q))  # Do not teach the local grammar the held-out phrase.
        result=self.proposal(q)
        self.assertEqual(result['status'],'ready',result)
        self.assertEqual(result['goal']['research']['mode'],'filter_complete')
        self.assertEqual(result['goal']['research']['target_count'],3)
        self.assertEqual(set(result['goal']['document_fields']),{'功能与主治','注意'})

    def test_no_lexical_evidence_is_not_a_mode_contradiction(self):
        q='甘草、陈皮、大黄的功效和注意，仅把这两栏都登记过的对象放进表格。'
        self.assertFalse(mode_constraints(q)['known_complete_filter'])
        self.assertEqual(self.proposal(q)['status'],'ready')

    def test_explicit_all_match_request_cannot_become_audit_or_top_n(self):
        q='甘草、陈皮、大黄哪些功效和注意都有记录，做成表'
        for mode,count in [('audit_named',None),('select_complete',2)]:
            result=compile_draft(draft(names=list(self.catalog),mode=mode,target_count=count),q,self.catalog)
            self.assertEqual(result['failure']['code'],'completeness_mode_mismatch')

    def test_explicit_preserved_rows_cannot_be_dropped_by_filter(self):
        for tail in ('不要筛选，全部保留','每味药材都保留','缺失的也保留','保留全部记录'):
            q='整理甘草、陈皮、大黄的功效和注意，'+tail
            self.assertEqual(self.proposal(q)['failure']['code'],'completeness_mode_mismatch',q)
            good=compile_draft(draft(names=list(self.catalog)),q,self.catalog)
            self.assertEqual(good['status'],'ready',(q,good))

    def test_missing_record_selection_cannot_become_positive_filter(self):
        for tail in ('筛出注意没有记录的','哪些注意缺失','只保留功效不齐全的'):
            q='甘草、陈皮、大黄的功效和注意，'+tail
            self.assertTrue(mode_constraints(q)['missing_selection'])
            self.assertEqual(self.proposal(q)['failure']['code'],'completeness_mode_mismatch',q)

    def test_explicit_n_still_binds_mode_and_count(self):
        q='从甘草、陈皮、大黄中挑两条功效和注意齐全的，做成表'
        for mode,count in [('filter_complete',None),('audit_named',None),('select_complete',1)]:
            bad=compile_draft(draft(names=list(self.catalog),mode=mode,target_count=count),q,self.catalog)
            self.assertEqual(bad['status'],'needs_input',(mode,bad))
        good=compile_draft(draft(names=list(self.catalog),mode='select_complete',target_count=2),q,self.catalog)
        self.assertEqual(good['status'],'ready',good)
        q='甘草、陈皮、大黄中选齐全的功效和注意记录，做成表'
        wrong=compile_draft(draft(names=list(self.catalog),mode='select_complete',target_count=2),q,self.catalog)
        self.assertEqual(wrong['failure']['code'],'selection_requirement_mismatch')

    def test_object_coverage_fields_and_negations_remain_required(self):
        q='甘草、陈皮、大黄中，哪些同时有药典功效和注意记载，做表'
        with self.assertRaises(ValueError):self.proposal(q,names=['甘草','大黄'])
        self.assertEqual(self.proposal(q,document_fields=['功能与主治'])['failure']['code'],'field_requirement_mismatch')
        q='甘草、陈皮、大黄的功效做表，不要注意'
        self.assertEqual(self.proposal(q)['failure']['code'],'field_requirement_mismatch')

    def test_public_failure_does_not_assert_the_model_caused_rejection(self):
        self.assertNotIn('模型改变',public_failure('completeness_mode_mismatch')['message'])

    def test_accepted_unknown_phrase_checks_whole_pool_and_delivers_zero_match_reasons(self):
        from tcm.dataops.research_graph import ResearchEngine
        from tcm.dataops.research_model import ResearchFixed
        from tcm.dataops.workspace import Workspaces
        from tcm.dataops.artifacts import verify
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);records,sources=make_sources(root)
            catalog={n:[records[n]['source_record_id']] for n in ('Alpha','Beta')}
            q='Alpha、Beta中，哪些同时有药典功效和注意记载？只把符合条件的整理成表。'
            proposal=compile_draft(draft(mode='filter_complete'),q,catalog)
            self.assertEqual(proposal['status'],'ready',proposal)
            store=Workspaces(root/'work')
            with sources() as source:
                doc=store.reserve_agent('owner','unknown-filter',proposal['goal'],source.revision,question=q)[0]
            result=ResearchEngine(store,'owner',doc['id'],sources,ResearchFixed()).run()
            self.assertEqual(result['status'],'complete',result.get('error'))
            self.assertEqual(result['result']['rows'],[])  # Neither fixture has 注意.
            research=result['result']['research']
            self.assertEqual(research['reviewed_count'],2)
            self.assertEqual(research['qualified_count'],0)
            self.assertTrue(all(c['reasons'] for c in research['candidate_states']))
            self.assertEqual(verify(store.root/doc['id']/result['artifact']['filename'],result['result'])['status'],'passed')
