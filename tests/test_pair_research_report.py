import asyncio
import copy
import unittest
from tcm.pair_research_report import run_pair_report, validate_pair, verify_targets, source_pair


def report(name):
    source={'file_sha256':name*64}
    return {'status':'complete','herb':name,
            'text':{'status':'candidates','hits':[{'label':'功能','text':'synthetic-'+name,'chunk_id':name}]},
            'structured':{'status':'ok','source_record_id':name,'source':source,'dataset_version':'v1',
                          'rows':[{'label':'科','value':'same','source':{'json_pointer':'/test'}}]}}


def targets(selection):
    ids=[r['source_record_id'] for r in selection['records']]
    return {'status':'ok','source_ids':ids,'dataset_version':selection['version'],'total':1,
            'items':[{'identifier':'P12345','sources':[{'source_record_id':i,'witnesses':[{'source_pointer':'/x','raw_row':{'id':'P12345'}}]} for i in ids]}]}


class PairTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_subgraphs_then_source_bound_dependency(self):
        import json
        entered=set();ready=asyncio.Event();events=[]
        async def researcher(payload):
            entered.add(payload['herb'])
            if len(entered)==2:ready.set()
            await asyncio.wait_for(ready.wait(),1)
            return report(payload['herb'])
        async def backend(branch,herb,question):
            self.assertEqual(entered,{'a','b'});self.assertEqual(branch,'shared_targets')
            selection=json.loads(question);self.assertEqual([r['source_record_id'] for r in selection['records']],['a','b'])
            return targets(selection)
        async def save(snapshot):events.append(snapshot)
        result=await run_pair_report({'herbs':['a','b'],'question':'功能'},researcher=researcher,target_backend=backend,on_state=save)
        self.assertEqual(result['status'],'complete');self.assertEqual(events[-1]['tool_calls'],5)
        self.assertEqual({r['comparison'] for r in result['rows']},{'same_display_value','different_display_value'})
        self.assertFalse(result['identity_merged'])

    async def test_actual_nested_report_graph(self):
        from tcm.cross_source_report import run_report
        import json
        async def backend(name, herb, question):
            return report(herb)['text' if name=='text' else 'structured']
        async def researcher(payload):return await run_report(payload,backend=backend)
        async def target_backend(branch,herb,question):return targets(json.loads(question))
        result=await run_pair_report({'herbs':['a','b'],'question':'功能'},researcher=researcher,target_backend=target_backend)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(len(result['rows']),2)

    async def test_missing_side_preserves_other_without_target_call(self):
        async def researcher(payload):
            if payload['herb']=='b':raise ValueError('private')
            return report('a')
        async def forbidden(*args):self.fail('must not query unresolved sources')
        result=await run_pair_report({'herbs':['a','b'],'question':'功能'},researcher=researcher,target_backend=forbidden)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['targets']['status'],'unresolved_sources')
        self.assertTrue(all(r['comparison']=='missing' for r in result['rows']))

    async def test_timeout_keeps_both_reports(self):
        async def researcher(payload):return report(payload['herb'])
        async def backend(*args):raise asyncio.TimeoutError()
        result=await run_pair_report({'herbs':['a','b'],'question':'功能'},researcher=researcher,target_backend=backend)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['targets']['status'],'timeout')
        self.assertEqual(len(result['source_reports']),2)

    async def test_cancellation_cascades(self):
        ready=asyncio.Event();stopped=[]
        async def researcher(payload):
            ready.set()
            try:await asyncio.sleep(10)
            finally:stopped.append(payload['herb'])
        task=asyncio.create_task(run_pair_report({'herbs':['a','b'],'question':'x'},researcher=researcher))
        await asyncio.wait_for(ready.wait(),2);await asyncio.sleep(.01);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(set(stopped),{'a','b'})


class ContractTests(unittest.TestCase):
    def test_names_versions_and_alias_collision(self):
        for names in [['a','a'],['A','ａ'],['a'],['','b']]:
            with self.assertRaises(ValueError):validate_pair(names)
        left,right=report('a'),report('b')
        right['structured']['dataset_version']='v2'
        self.assertEqual(source_pair([left,right])[1],'version_mismatch')
        self.assertEqual(source_pair([left,left])[1],'same_source')

    def test_two_sided_witnesses_and_ids_are_required(self):
        selection,_=source_pair([report('a'),report('b')]);valid=targets(selection)
        self.assertEqual(verify_targets(valid,selection)['status'],'ok')
        for mutation in ['wrong_source','missing_witness','wrong_version','wrong_count','duplicate']:
            bad=copy.deepcopy(valid)
            if mutation=='wrong_source':bad['items'][0]['sources'][1]['source_record_id']='c'
            if mutation=='missing_witness':bad['items'][0]['sources'][1]['witnesses']=[]
            if mutation=='wrong_version':bad['dataset_version']='v2'
            if mutation=='wrong_count':bad['total']=0
            if mutation=='duplicate':bad['items']*=2;bad['total']=2
            self.assertEqual(verify_targets(bad,selection)['status'],'invalid_evidence')
