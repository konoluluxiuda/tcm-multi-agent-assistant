"""Business scoring keeps incomplete evidence and boundary responses out of completion."""
import copy
import json
from pathlib import Path
import unittest

from scripts.eval_business_acceptance import SourceChecks, assess, validate_suite


class BusinessAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.field={'label':'贮藏','text':'合成保存记录','raw_text':'合成保存记录','evidence':[{'page':1}]}
        self.refs=SourceChecks({'entries':[{'id':'entry-a','name_zh_candidate':'示例叶','fields':[self.field]}],
                                'herb_fields':{},'counts':{}})
        self.hit={'entry_id':'entry-a','field_index':0,'name':'示例叶',**self.field}
        self.case={'kind':'pharmacopoeia','name':'示例叶','fields':['贮藏']}
        self.body={'research':{'status':'candidates','tool_result':{'hits':[self.hit]}}}

    def test_missing_required_field_is_partial_even_when_tool_claims_candidates(self):
        case={**self.case,'fields':['贮藏','注意']}
        self.assertEqual(assess(case,200,self.body,self.refs)['outcome'],'partial')
        self.assertEqual(assess(self.case,200,self.body,self.refs)['outcome'],'completed_local_scope')

    def test_changed_text_or_anchors_cannot_pass_original_source_checks(self):
        for key,value in [('text','补造内容'),('evidence',[{'page':99}]),('name','其他药')]:
            body=copy.deepcopy(self.body);body['research']['tool_result']['hits'][0][key]=value
            self.assertEqual(assess(self.case,200,body,self.refs)['outcome'],'incorrect_or_unverified')

    def test_clarification_403_and_failure_are_not_positive_completion(self):
        body={'research':{'status':'clarify_entity','tool_result':None}}
        self.assertEqual(assess(self.case,200,body,self.refs)['outcome'],'clarification')
        self.assertEqual(assess(self.case,403,{},self.refs)['outcome'],'unsupported_intent')
        self.assertEqual(assess(self.case,503,{},self.refs)['outcome'],'technical_failure')

    def test_disabled_tool_and_personal_medication_boundary_are_separate_outcomes(self):
        disabled={'kind':'boundary','expected':'pubmed_disabled'}
        self.assertEqual(assess(disabled,503,{'pubmed':{'status':'pubmed_disabled'}},self.refs)['outcome'],'tool_unavailable')
        personal={'kind':'boundary','expected':'personal_medication_boundary'}
        body={'assistant':{'topic':'personal_medication_boundary','status':'clarification_needed'}}
        self.assertEqual(assess(personal,200,body,self.refs)['outcome'],'appropriate_refusal')
        body['research']=self.body['research']
        self.assertEqual(assess(personal,200,body,self.refs)['outcome'],'incorrect_or_unverified')

    def test_export_required_task_cannot_pass_without_download(self):
        case={'kind':'comparison','names':['示例叶','第二草'],'fields':['贮藏'],'export':True}
        entries=copy.deepcopy(self.refs.reference);second=copy.deepcopy(entries['entries'][0])
        second.update(id='entry-b',name_zh_candidate='第二草');entries['entries'].append(second)
        second_hit={**self.hit,'entry_id':'entry-b','name':'第二草'}
        comp={'names':case['names'],'fields':case['fields'],'groups':[
            {'name':name,'cells':[{'field':'贮藏','status':'candidates','result':{'hits':[hit]}}]}
            for name,hit in zip(case['names'],[self.hit,second_hit])]}
        body={'research':{'status':'candidates','tool_result':{'comparison':comp}}}
        self.assertEqual(assess(case,200,body,SourceChecks(entries))['outcome'],'incorrect_or_unverified')

    def test_driver_creates_separate_clients_and_cleans_every_session(self):
        import io
        import tempfile
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from scripts.eval_business_acceptance import run
        calls=[];clients=[]
        class Reply:
            def __init__(self,code,data):self.code=code;self.data=data;self.headers={}
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,*args):return json.dumps(self.data).encode()
        class Client:
            def open(self,request,timeout):
                calls.append((id(self),request.method,request.full_url,request.data))
                if request.method=='POST':return Reply(503,{'pubmed':{'status':'pubmed_disabled'}})
                return Reply(200,{'status':'cleared'})
        def client(*args):
            obj=Client();clients.append(obj);return obj
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cases=[{'id':str(n),'group':'boundary','role':'boundary','kind':'boundary',
                    'expected':'pubmed_disabled','query':'/pubmed synthetic'} for n in range(2)]
            (root/'suite.json').write_text(json.dumps({'cases':cases}))
            (root/'references.json').write_text(json.dumps({'entries':[]}))
            with patch('scripts.eval_business_acceptance.verify_frozen',return_value={'suite_sha256':'x','runtime':{}}), \
                 patch('scripts.eval_business_acceptance.urllib.request.build_opener',side_effect=client),redirect_stdout(io.StringIO()):
                self.assertEqual(run(root),0)
            summary=json.loads((root/'summary.json').read_text())
            self.assertEqual(summary['boundary_tasks']['outcomes'],{'tool_unavailable':2})
            self.assertTrue(summary['cleanup_passed'])
            self.assertEqual(len(clients),2)
            self.assertEqual([method for _,method,_,_ in calls],['POST','DELETE','POST','DELETE'])
            self.assertTrue(all(json.loads(data)['answer_mode']=='auto' for _,method,_,data in calls if method=='POST'))

    def test_suite_discloses_designed_origin_and_keeps_boundary_denominator_separate(self):
        suite=json.loads((Path(__file__).resolve().parents[1]/'eval/business_acceptance_v1.json').read_text())
        validate_suite(suite)
        self.assertEqual(sum(c['role']=='positive_task' for c in suite['cases']),17)
        self.assertEqual(sum(c['role']=='boundary' for c in suite['cases']),7)
        suite['independent_acceptance']=True
        with self.assertRaises(AssertionError):validate_suite(suite)


if __name__=='__main__':unittest.main()
