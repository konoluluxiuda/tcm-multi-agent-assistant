"""Mutations of actual observations; bad data must never grade as complete."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from pydantic import ValidationError
from tcm.evaluation.requirements_benchmark import Scenario,CaseResult,ResultStep,score,load_scenarios,TableExpectation,digest


def scenario():
    return Scenario.model_validate({'id':'D1','category':'multi_field','turns':[{'id':'t1','question':'Alpha和Beta的药用部位'}],
        'expected':{'task_class':'supported','objects':['Alpha','Beta'],'requirements':[
            {'object_ref':n,'namespace':'etcm','name':'medicinal_part'} for n in ('o1','o2')],
            'source_assertions':[{'object_ref':n,'namespace':'etcm','name':'medicinal_part','source_document':f'{obj}.json','value_contains':val}
                for n,obj,val in [('o1','Alpha','Leaf'),('o2','Beta','Root')]]}})


def result():
    return CaseResult(scenario_id='D1',policy='V',steps=[ResultStep(turn_id='t1',http_status=200,terminal_state='success',objects=['Alpha','Beta'],
        fields=[{'object_ref':n,'namespace':'etcm','name':'medicinal_part','value':val,'state':'present'} for n,val in [('Alpha','Leaf'),('Beta','Root')]],
        sources=[{'object_ref':n,'namespace':'etcm','name':'medicinal_part','source_document':f'{n}.json','value':val} for n,val in [('Alpha','Leaf'),('Beta','Root')]])])


class ScoreTests(unittest.TestCase):
    def test_observed_witnesses_pass(self):self.assertTrue(score(scenario(),result()).completed)
    def test_unbound_field_cannot_satisfy_two_objects(self):
        r=result();r.steps[0].fields=[{'namespace':'etcm','name':'medicinal_part'}]
        self.assertFalse(score(scenario(),r).completed)
    def test_wrong_source_value_even_if_text_contains_reference(self):
        r=result();r.steps[0].response_text='Leaf Root';r.steps[0].sources[0]['value']='wrong'
        self.assertFalse(score(scenario(),r).completed)
    def test_correct_source_but_wrong_answer_value_rejected(self):
        r=result();r.steps[0].fields[0]['value']='wrong'
        self.assertFalse(score(scenario(),r).completed)
    def test_wrong_namespace_and_field_rejected(self):
        for key in ('namespace','name','source_document','object_ref'):
            with self.subTest(key=key):
                r=result();r.steps[0].sources[0][key]='wrong';self.assertFalse(score(scenario(),r).completed)
    def test_source_hash_and_page_checked(self):
        s=scenario();s.expected.source_assertions[0].source_sha256='a'*64;s.expected.source_assertions[0].page=3
        self.assertFalse(score(s,result()).completed)
    def test_missing_assertions_checked(self):
        s=scenario();s.expected.missing_assertions=[s.expected.requirements[0]]
        self.assertFalse(score(s,result()).completed)
    def test_no_source_reference_does_not_mean_success(self):
        s=scenario();s.expected.source_assertions=[]
        self.assertFalse(score(s,result()).completed)
    def test_missing_turn_and_wrong_id_rejected(self):
        s=scenario();raw=s.model_dump();raw['turns'].append({'id':'t2','question':'再加一个字段'})
        self.assertFalse(score(Scenario.model_validate(raw),result()).completed)
        r=result();r.steps[0].turn_id='other';self.assertFalse(score(s,r).completed)
    def test_wrong_case_rejected(self):
        r=result();r.scenario_id='other';self.assertFalse(score(scenario(),r).completed)
    def test_http503_not_success(self):
        r=result();r.steps[0].http_status=503;self.assertFalse(score(scenario(),r).completed)
    def test_partial_is_not_automatic_success(self):
        r=result();r.steps[0].terminal_state='partial';self.assertFalse(score(scenario(),r).completed)
    def test_fake_artifact_not_accepted(self):
        s=scenario();raw=s.model_dump();raw['expected'].update(delivery='table',table={'row_policy':'keep_all'})
        r=result();r.artifact_sha256='a'*64
        self.assertFalse(score(Scenario.model_validate(raw),r).completed)
    def test_relation_pairing_is_preserved(self):
        raw=scenario().model_dump();raw['expected']['requirements']=[{'kind':'relation','namespace':'etcm','name':'common_target','object_refs':['o1','o2']}]
        raw['expected']['source_assertions']=[{'object_ref':x,'namespace':'etcm','name':'common_target','value_contains':'P01','source_document':'targets'} for x in ('o1','o2')]
        s=Scenario.model_validate(raw);r=result();r.steps[0].relations=[{'namespace':'etcm','name':'common_target','object_refs':['Alpha','Gamma']}]
        self.assertFalse(score(s,r).dimensions['requirements'])
        r.steps[0].relations[0]['object_refs']=['Beta','Alpha']
        self.assertTrue(score(s,r).dimensions['requirements'])
    def test_boundary_only_correct_http_and_terminal(self):
        s=Scenario.model_validate({'id':'B','category':'boundary','turns':[{'id':'t1','question':'治疗我'}],
            'expected':{'task_class':'boundary','allowed_terminal_states':['unsupported']}})
        r=CaseResult(scenario_id='B',policy='R',steps=[ResultStep(turn_id='t1',http_status=403,terminal_state='unsupported')])
        self.assertTrue(score(s,r).completed)
        r.steps[0].http_status=503;self.assertFalse(score(s,r).completed)
    def test_unknown_outcome_never_passes(self):
        r=result();r.status='unknown';self.assertFalse(score(scenario(),r).completed)
    def test_empty_supported_requirements_rejected(self):
        raw=scenario().model_dump();raw['expected']['requirements']=[]
        with self.assertRaises(ValidationError):Scenario.model_validate(raw)
    def test_top_count_and_duplicate_cases_rejected(self):
        with self.assertRaises(ValidationError):TableExpectation(row_policy='top_n')
        with self.assertRaises(ValueError):load_scenarios(json.dumps([scenario().model_dump()]*2))


class ArtifactTests(unittest.TestCase):
    def test_reopens_real_file_and_rejects_modified_cell_and_parent(self):
        from tests.dataops_fixture import make_sources
        from tcm.dataops.research_contracts import ResearchGoal
        from tcm.dataops.research_graph import ResearchEngine
        from tcm.dataops.research_model import ResearchFixed
        from tcm.dataops.workspace import Workspaces
        from tcm.evaluation.api_runner import observe_table
        from openpyxl import load_workbook
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);_,sources=make_sources(root/'sources');store=Workspaces(root/'work')
            goal=ResearchGoal(query={'names':['Alpha','Beta']},attributes=['medicinal_part'],document_fields=['功能与主治'],research={'mode':'audit_named'})
            with sources() as src:doc,_=store.reserve_agent('test','artifact-test',goal.model_dump(),src.revision,question='test fields')
            doc=ResearchEngine(store,'test',doc['id'],sources,ResearchFixed()).run()
            path=store.root/doc['id']/doc['artifact']['filename']
            s=scenario();raw=s.model_dump();raw['expected'].update(delivery='table',allowed_terminal_states=['complete'],table={'row_policy':'keep_all'})
            # This assertion isolates workbook validation; golden source matching is tested separately.
            s=Scenario.model_validate(raw);r=result();observe_table(r.steps[0],doc,path)
            self.assertTrue(score(s,r).dimensions['table'])
            book=load_workbook(path);book['结果']['D2']='tampered';book.save(path);book.close()
            r.steps[0].artifact_sha256=digest(path)
            self.assertFalse(score(s,r).dimensions['table'])
