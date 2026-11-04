"""A successful route is insufficient: fields, subjects and witnesses must agree."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from scripts import query_answer_checks as checks
from scripts import eval_user_chat_replay as replay


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'etcm_herbs').mkdir()
        self.proofs={}
        for name,identifier in [('Alpha','P001'),('Beta','P001')]:
            doc={'data':[{'id':'base_information','value':[{'key':'Medicinal Part','value':'Root'}]},
                {'id':'related_table','value':[{'type':'ingredient','value':[{'Ingredient Name':['compound']}]},
                                             {'type':'target','value':[{'UniProt Accession Number':[identifier]}]}]}]}
            path=self.root/'etcm_herbs'/(name+'.json');path.write_text(json.dumps(doc))
            self.proofs[name]={'relative_file_path':'etcm_herbs/'+name+'.json','file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        self.attr={'attributes':[{'source':'Alpha','fields':['medicinal_part']}]}

    def cell(self):
        return {'state':'present','value':'Root','source':{**self.proofs['Alpha'],'json_pointer':'/data/0/value/0/value','raw_value':'Root'}}

    def test_direct_and_queued_attributes_pass_the_same_raw_contract(self):
        direct={'tcm':{'records':[{'fields':{'medicinal_part':self.cell()}}]}}
        report={'task':{'result':{'display':{'blocks':[{'tool':'herb_attributes','field_rows':[
            {'field':'药用部位','value':'根','original_value':'Root','evidence':self.cell()['source']}]}]}}}}
        for value in (direct,report):self.assertGreater(checks.check(value,self.attr,self.root)['raw_witnesses_or_pdf_anchors_checked'],0)

    def test_wrong_subject_missing_fields_or_fabricated_value_cannot_pass(self):
        for cell in ({**self.cell(),'source':{**self.cell()['source'],**self.proofs['Beta']}},
                     {**self.cell(),'value':'Invented'}, {**self.cell(),'state':'missing'}):
            with self.assertRaises(ValueError):checks.check({'fields':{'medicinal_part':cell}},self.attr,self.root)
        with self.assertRaises(ValueError):checks.check({'status':'success'},self.attr,self.root)

    def test_omitted_attribute_cannot_be_borrowed_from_another_subject(self):
        expect={'attributes':[{'source':'Alpha','fields':['medicinal_part','family']}]}
        with self.assertRaises(ValueError):checks.check({'fields':{'medicinal_part':self.cell()}},expect,self.root)

    def relation(self,display=False):
        option={'source_record_id':'alpha-id','source':self.proofs['Alpha']}
        if display:
            return {'tool':'herb_relation_records','report_kind':'relations','title':'Alpha · 成分','total':1,
                    'source':{'groups':[{'options':[option]}]},'field_rows':[{'field':'compound','value':'compound',
                    'evidence':{'source_record_id':'alpha-id','json_pointer':'/data/1/value/0/value/0','raw_row':{'Ingredient Name':['compound']}}}]}
        return {'kind':'ingredient','groups':[{'options':[option]}],'data':{'total':1,'items':[
            {'source_pointer':'/data/1/value/0/value/0','raw_row':{'Ingredient Name':['compound']}}]}}

    def test_relation_count_and_witness_match_for_direct_or_report_page(self):
        expect={'relations':[{'kind':'ingredient','sources':['Alpha']}]}
        for display in (False,True):self.assertEqual(checks.check(self.relation(display),expect,self.root)['raw_witnesses_or_pdf_anchors_checked'],1)
        bad=self.relation();bad['data']['total']=2
        with self.assertRaises(ValueError):checks.check(bad,expect,self.root)
        bad=self.relation();bad['data']['items'][0]['source_pointer']='/data/1/value/1/value/0'
        with self.assertRaises(ValueError):checks.check(bad,expect,self.root)

    def test_common_target_requires_both_original_witnesses(self):
        rel={'kind':'common_target','groups':[{'options':[{'source_record_id':n,'source':self.proofs[n]}]} for n in ('Alpha','Beta')],
             'data':{'total':1,'items':[{'identifier':'P001','sources':[
                 {'source_record_id':n,'witnesses':[{'source_pointer':'/data/1/value/1/value/0',
                   'raw_row':{'UniProt Accession Number':['P001']}}]} for n in ('Alpha','Beta')]}]}}
        expect={'relations':[{'kind':'common_target','sources':['Alpha','Beta']}]}
        self.assertEqual(checks.check(rel,expect,self.root)['raw_witnesses_or_pdf_anchors_checked'],2)
        rel['data']['items'][0]['sources'].pop()
        with self.assertRaises(ValueError):checks.check(rel,expect,self.root)

    def test_direct_or_report_pharma_requires_all_fields_for_the_exact_subject(self):
        proof=[{'block_id':'block','source':{'source_pdf_page':3,'source_sha256':'a'*64}}]
        expect={'pharmacopoeia':[{'name':'示例草','fields':['贮藏']}]}
        direct={'name':'示例草','label':'贮藏','text':'原文','evidence':proof}
        report={'tool':'pharmacopoeia_lookup','title':'示例草 · 药典','field_rows':[{'field':'贮藏','value':'原文','evidence':proof}]}
        for value in (direct,report):self.assertEqual(checks.check(value,expect,self.root)['raw_witnesses_or_pdf_anchors_checked'],1)
        for value in ({**direct,'name':'另一草'},{**direct,'evidence':[]},{**direct,'label':'注意'}):
            with self.assertRaises(ValueError):checks.check(value,expect,self.root)

    def test_empty_contract_unknown_fields_and_unfrozen_sources_are_rejected(self):
        for expect in ({},{'attributes':[]},{'attributes':[{'source':'Alpha','fields':[]}]},
                       {'attributes':[{'source':'Alpha','fields':['procurement_price']}]},
                       {'relations':[{'kind':'common_target','sources':['Alpha','Alpha']}]}):
            with self.assertRaises(ValueError):checks.validate(expect)
        suite={'version':'test','authorship':{'kind':'developer_authored_frozen','independent_acceptance':False},
               'cases':[{'id':'T01','role':'business','turns':[{'query':'original','answer':self.attr}]}]}
        with self.assertRaises(ValueError):replay.validate_suite(suite)
        suite['cases'][0]['turns'][0]['reference_sources']=['Alpha'];replay.validate_suite(suite)

    def test_changed_answer_grader_blocks_before_any_submission(self):
        from tests.test_user_chat_replay import ReplayTests,suite
        fixture=ReplayTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        data=suite();turn=data['cases'][0]['turns'][0]
        turn.update(answer=self.attr,reference_sources=['Alpha']);turn.pop('checks')
        path=fixture.root/'etcm_herbs';path.mkdir()
        (path/'Alpha.json').write_text((self.root/'etcm_herbs/Alpha.json').read_text())
        factory=fixture.factory();out=fixture.prepare(data,client_factory=factory)
        protocol=json.loads((out/'protocol.private.json').read_text());self.assertIn('answer_evaluator_sha256',protocol)
        original=replay.digest
        def changed(path):return 'changed' if Path(path).name=='query_answer_checks.py' else original(path)
        with patch.object(replay,'digest',side_effect=changed),self.assertRaisesRegex(ValueError,'answer evaluator'):
            replay.execute(out,client_factory=factory)
        self.assertFalse(fixture.posts)

    def test_private_externalized_observations_are_downloaded_and_hash_checked(self):
        raw=json.dumps(self.relation()).encode();task_id='a'*32
        task={'status':'complete','result':{'run':{'evidence':{'obs-1':{'externalized':True,'content_sha256':hashlib.sha256(raw).hexdigest()}}},
             'display':{'blocks':[{'tool':'herb_relation_records','preview_omitted':True}]}}}
        paths=[]
        def respond(req):
            paths.append(req.url.path)
            return httpx.Response(200,content=raw) if req.url.path.endswith('/download') else httpx.Response(200,json=task)
        with httpx.Client(base_url='http://127.0.0.1:9999',transport=httpx.MockTransport(respond)) as client:
            out=self.root/'full';out.mkdir()
            result=replay.read_query_result(client,task_id,out)
            receipt=checks.check(result,{'relations':[{'kind':'ingredient','sources':['Alpha']}]},self.root)
            self.assertEqual(receipt['raw_witnesses_or_pdf_anchors_checked'],1)
            self.assertEqual(paths[-1],'/api/research/runs/'+task_id+'/evidence/obs-1/download')
            task['result']['run']['evidence']['obs-1']['content_sha256']='b'*64
            corrupt=self.root/'corrupt';corrupt.mkdir()
            with self.assertRaisesRegex(ValueError,'checksum'):replay.read_query_result(client,task_id,corrupt)
