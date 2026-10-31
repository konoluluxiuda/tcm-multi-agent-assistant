from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tests.dataops_fixture import make_sources
from tcm.dataops.data_access import canonical
from tcm.dataops.evaluation import SourceOracle, run_case
from tcm.dataops.workspace import Workspaces


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);_,self.factory=make_sources(self.root/'fixture')
        self.oracle=SourceOracle(self.root/'fixture')
        self.suite=json.loads(Path('eval/dataops_tasks_v1.json').read_text())

    def case(self,identifier):return deepcopy(next(c for c in self.suite['cases'] if c['case_id']==identifier))

    def test_suite_split_categories_and_provenance_are_explicit(self):
        from collections import Counter
        self.assertEqual(len(self.suite['cases']),30)
        self.assertEqual(Counter(c['split'] for c in self.suite['cases']),{'development':18,'prospective_holdout':12})
        self.assertEqual(Counter(c['category'] for c in self.suite['cases']),self.suite['categories'])
        self.assertIn('not_free_text',self.suite['scope'])

    def test_fixed_and_once_plan_keep_human_handling(self):
        case=self.case('B04');reference=self.oracle.reference(case)
        for strategy in ('F','P'):
            result=run_case(case,reference,self.oracle,strategy,self.factory,self.root/strategy)
            self.assertEqual(result['status'],'passed',result)
            self.assertEqual(result['external_model_calls'],0)

    def test_reference_rejects_fabricated_values_and_document_position(self):
        case=self.case('B01');reference=self.oracle.reference(case)
        result=run_case(case,reference,self.oracle,'A',self.factory,self.root/'run')
        self.assertEqual(result['status'],'passed',result)
        store=Workspaces(self.root/'run');doc=store.get('benchmark-owner',result['workspace_id'])
        original=deepcopy(doc);row=doc['result']['rows'][0]
        row['cells']['功能与主治']['value']='fabricated'
        self.assertTrue(self.oracle.score(case,reference,doc,store))
        doc=original;ref=doc['result']['rows'][0]['cells']['功能与主治']['evidence_ids'][0]
        doc['result']['evidence'][ref]['source']['chunk_id']='invented'
        self.assertIn('invented_document_position',self.oracle.score(case,reference,doc,store))

    def test_reference_checks_raw_json_pointer_and_page_evidence(self):
        case=self.case('B01');reference=self.oracle.reference(case)
        result=run_case(case,reference,self.oracle,'A',self.factory,self.root/'run')
        store=Workspaces(self.root/'run');doc=store.get('benchmark-owner',result['workspace_id'])
        structured=doc['result']['rows'][0]['cells']['medicinal_part']['evidence_ids'][0]
        doc['result']['evidence'][structured]['source']['json_pointer']='/wrong'
        self.assertIn('wrong_structured_position',self.oracle.score(case,reference,doc,store))
        doc=store.get('benchmark-owner',result['workspace_id'])
        ref=doc['result']['rows'][0]['cells']['功能与主治']['evidence_ids'][0]
        doc['result']['evidence'][ref]['source']['payload']['evidence'][0]['source']['source_pdf_page']=999
        self.assertIn('altered_page_or_document_witness',self.oracle.score(case,reference,doc,store))

    def test_incremental_reference_is_independent_of_workbook_output(self):
        case=self.case('R04');reference=self.oracle.reference(case)
        frozen=canonical(reference)
        result=run_case(case,reference,self.oracle,'A',self.factory,self.root/'run')
        self.assertEqual(result['status'],'passed',result)
        self.assertEqual(canonical(reference),frozen)

    def test_failed_parent_is_reported_without_creating_child(self):
        from tcm.dataops.evaluation import MeasuredDriver
        case=self.case('R01');reference=self.oracle.reference(case)
        class FailedDriver(MeasuredDriver):
            def decide(inner,context):
                inner.context_bytes.append(len(canonical(context).encode()))
                raise TimeoutError('synthetic unknown outcome')
        result=run_case(case,reference,self.oracle,'A',self.factory,self.root/'failed-parent',
            driver_factory=lambda strategy,case:FailedDriver(strategy,case['goal']))
        self.assertEqual(result['outcome'],'blocked_parent',result)
        self.assertEqual(result['errors'],['parent_missing_delivery'])
        self.assertEqual(result['parent_error'],'model_dispatch_unknown')
        self.assertFalse(result['child_created'])
        self.assertEqual(len(Workspaces(self.root/'failed-parent').listing('benchmark-owner')),1)

    def test_budget_limited_artifact_is_not_reported_as_completed_delivery(self):
        from tcm.dataops.evaluation import MeasuredDriver
        case=self.case('B01');reference=self.oracle.reference(case)
        class BudgetDriver(MeasuredDriver):
            def decide(inner, context):
                inner.context_bytes.append(len(canonical(context).encode()))
                actions=['query_records','inspect_sources','lookup_document_fields','compare_fields',
                         'read_evidence','build_artifact','compare_fields','verify_delivery']
                tool=actions[len(inner.context_bytes)-1]
                args={'purpose':'重现预算不足但已生成文件'}
                if tool=='read_evidence': args['evidence_ids']=context['evidence_refs'][:1]
                return {'tool':tool,'arguments':args}
        result=run_case(case,reference,self.oracle,'A',self.factory,self.root/'budget-tail',
            driver_factory=lambda strategy,case:BudgetDriver(strategy,case['goal']))
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['stop_reason'],'model_budget')
        self.assertTrue(result['artifact_present'])
        self.assertTrue(result['verification_action_completed'])
        self.assertFalse(result['finish_action_completed'])


if __name__=='__main__':unittest.main()
