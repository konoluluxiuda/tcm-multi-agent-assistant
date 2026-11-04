from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agents.conversation_store import ConversationStore
from tests.dataops_fixture import make_sources
from tests.test_research_question import draft
from tcm.dataops import diagnostics as d
from tcm.dataops.api import create_router
from tcm.dataops.goal_model import GoalResponseRejected
from tcm.dataops.research_question import request_proposal,local_proposal,understanding_input
from tcm.request_observability import install


class Model:
    max_tokens=1200;max_retries=0;model_name='synthetic-diagnostic'
    def __init__(self,args,tool='propose_research_goal'):self.args=args;self.tool=tool;self.calls=0
    def bind_tools(self,*args,**kwargs):return self
    def invoke(self,messages):
        self.calls+=1
        return AIMessage(content='',tool_calls=[{'id':'synthetic-call','name':self.tool,'args':self.args}],
                         usage_metadata={'input_tokens':10,'output_tokens':5,'total_tokens':15})


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.catalog={'Alpha':['sid-a'],'Beta':['sid-b']}

    def run_model(self,args,question='整理Alpha和Beta功效，生成表格'):
        model=Model(args)
        with d.request_context() as ctx:
            d.bind(self.root,'owner',question=question)
            try:result=request_proposal(model,question,self.catalog,authorized=True)
            except GoalResponseRejected as exc:result=exc
            receipt=d.Store(self.root).get('owner',ctx['request_id'])
        self.assertEqual(model.calls,1)
        return result,receipt

    def test_rejected_arguments_persist_before_validation_with_exact_object_difference(self):
        result,receipt=self.run_model(draft(names=['Alpha']))
        self.assertIsInstance(result,GoalResponseRejected)
        self.assertEqual(result.diagnostics['rejection']['code'],'mentioned_object_omitted')
        events=receipt['events'];raw=next(e for e in events if e['stage']=='model_return')
        reject=next(e for e in events if e['stage']=='proposal_validation')
        self.assertEqual(raw['details']['tool_calls'][0]['args']['names'],['Alpha'])
        self.assertEqual(reject['details']['expected_ids'],['sid-a','sid-b'])
        self.assertEqual(reject['details']['actual_ids'],['sid-a'])
        self.assertLess(events.index(raw),events.index(reject))

    def test_unknown_alias_and_schema_errors_have_safe_distinct_codes(self):
        result,_=self.run_model(draft(names=['Alpha','Unknown']))
        self.assertEqual(result.diagnostics['rejection']['code'],'unknown_alias')
        result,_=self.run_model(draft(document_fields=['private-invalid-value']))
        rejected=result.diagnostics['rejection']
        self.assertEqual(rejected['code'],'schema_validation_failed')
        self.assertNotIn('private-invalid-value',json.dumps(rejected))
        self.assertTrue(rejected['fields'])

    def test_invalid_tool_response_is_recorded_without_retry(self):
        model=Model({},tool='unavailable_tool')
        with d.request_context() as ctx:
            d.bind(self.root,'owner')
            with self.assertRaises(GoalResponseRejected) as error:
                request_proposal(model,'整理Alpha功效',{'Alpha':['a']},authorized=True)
            self.assertEqual(error.exception.diagnostics['rejection']['code'],'invalid_tool_call_shape')
            self.assertTrue(d.Store(self.root).get('owner',ctx['request_id'])['events'])
        self.assertEqual(model.calls,1)

    def test_host_default_columns_visible_without_changing_current_behavior(self):
        result,receipt=self.run_model(draft(attributes=['family','medicinal_part'],document_fields=[]),
                                      '整理Alpha和Beta的科名和药用部位，生成表格')
        self.assertEqual(result['status'],'ready')
        event=next(e for e in receipt['events'] if e['stage']=='field_normalization')
        self.assertEqual(event['details']['model_document_fields'],[])
        self.assertEqual(event['details']['normalized_document_fields'],['功能与主治','注意'])
        self.assertEqual(result['goal']['document_fields'],['功能与主治','注意'])

    def test_diagnostic_failure_does_not_change_proposal_or_trigger_retry(self):
        with patch.object(d.Store,'append',side_effect=OSError('offline synthetic storage failure')):
            result,_=self.run_model(draft())
        self.assertEqual(result['status'],'ready')

    def test_records_are_owner_scoped_and_persist_across_contexts(self):
        with d.request_context() as ctx:
            identifier=ctx['request_id'];d.bind(self.root,'owner');d.record('test','observed')
        self.assertTrue(d.Store(self.root).get('owner',identifier)['events'])
        with self.assertRaises(KeyError):d.Store(self.root).get('other-owner',identifier)
        with self.assertRaises(KeyError):d.Store(self.root).get('owner','../path')
        self.assertEqual(d.Store(self.root).path.stat().st_mode & 0o777,0o600)

    def test_worker_reattaches_original_id_instead_of_inheriting_first_http_request(self):
        with d.request_context() as first:
            origin=first['request_id'];d.bind(self.root,'owner');d.link_workspace('workspace')
        with d.request_context() as unrelated:
            with d.job_context(self.root,'owner','workspace'):
                self.assertEqual(d.CURRENT.get()['request_id'],origin);d.record('background','finished')
            self.assertEqual(d.CURRENT.get()['request_id'],unrelated['request_id'])
        self.assertTrue(any(e['stage']=='background' for e in d.Store(self.root).get('owner',origin)['events']))

    def test_concurrent_contexts_do_not_mix_events(self):
        def run(i):
            with d.request_context() as ctx:
                d.bind(self.root,'owner');d.record('parallel','observed',marker=i)
                return ctx['request_id'],i
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
        self.assertEqual(len({i for i,_ in results}),4)
        for identifier,marker in results:
            events=d.Store(self.root).get('owner',identifier)['events']
            self.assertEqual([e['details']['marker'] for e in events if e['stage']=='parallel'],[marker])

    def test_credentials_hidden_reasoning_and_invalid_json_are_not_exported(self):
        with patch.dict('os.environ',{'RAG_API_KEY':'sk-sensitive-diagnostic-fixture'}),d.request_context() as ctx:
            d.bind(self.root,'owner');d.record('return','received',args={'api_key':'must-hide','reasoning':'private-chain','value':'sk-sensitive-diagnostic-fixture'})
            text=json.dumps(d.Store(self.root).get('owner',ctx['request_id']))
            for secret in ('must-hide','private-chain','sk-sensitive-diagnostic-fixture'):self.assertNotIn(secret,text)
        raw='{"reasoning":"private-invalid-json"'
        value=d.model_return(SimpleNamespace(tool_calls=[],invalid_tool_calls=[{'name':'tool','args':raw}]))
        self.assertNotIn('private-invalid-json',json.dumps(value))
        self.assertEqual(value['invalid_tool_calls'][0]['args_sha256'],hashlib.sha256(raw.encode()).hexdigest())

    def test_unmapped_exception_has_source_location_without_message_or_locals(self):
        try:raise ValueError('private exception payload and password')
        except ValueError as exc:detail=d.validation_detail(exc)
        self.assertEqual(detail['code'],'contract_validation_failed')
        self.assertEqual(detail['location']['file'],'tests/test_diagnostic_chain.py')
        self.assertEqual(detail['location']['function'],'test_unmapped_exception_has_source_location_without_message_or_locals')
        self.assertNotIn('password',json.dumps(detail));self.assertNotIn('private exception',json.dumps(detail))

    def test_unreadable_diagnostic_source_keeps_original_rejection(self):
        with patch.object(Path,'read_bytes',side_effect=OSError('synthetic unreadable source')):
            result,_=self.run_model(draft(names=['Alpha']))
        self.assertIsInstance(result,GoalResponseRejected)
        self.assertEqual(result.diagnostics['rejection']['code'],'mentioned_object_omitted')
        self.assertIsNone(result.diagnostics['rejection']['location'])


class DiagnosticApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        _,self.sources=make_sources(self.root);self.memory=ConversationStore(self.root/'memory.sqlite3')
        for item in [patch.dict('os.environ',{'ENABLE_DATAOPS':'1','ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'fixed',
            'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'0','DATAOPS_ROOT':str(self.root/'work'),'RESEARCH_JOB_DB':str(self.root/'jobs.sqlite3'),
            'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':''}),patch('tcm.dataops.jobs.research_sources',self.sources)]:
            item.start();self.addCleanup(item.stop)
        self.app=FastAPI();self.app.include_router(create_router(lambda:self.memory,lambda:'cookie'));install(self.app)
        self.client=TestClient(self.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.client.get('/dataops/research')

    def test_http_header_links_to_background_delivery_and_replay_has_no_new_model_return(self):
        body={'question':'整理Alpha和Beta功效，生成表格','request_key':'diagnostic-api'}
        response=self.client.post('/api/dataops/research-questions',json=body)
        self.assertEqual(response.status_code,202,response.text)
        identifier=response.headers['X-Request-ID'];workspace=response.json()['workspace']['id']
        for _ in range(200):
            snapshot=self.client.get('/api/dataops/agent-runs/'+workspace).json()
            if snapshot['job']['status'] not in ('queued','running'):break
            time.sleep(.02)
        self.assertEqual(snapshot['workspace']['status'],'complete')
        diagnostic=self.client.get('/api/dataops/diagnostics/'+identifier)
        self.assertEqual(diagnostic.status_code,200)
        receipt=diagnostic.json();self.assertEqual(receipt['workspace_id'],workspace)
        self.assertEqual(receipt['langfuse_trace_id'],identifier)
        stages={e['stage'] for e in receipt['events']}
        self.assertTrue({'request','name_resolution','local_understanding','queue','execution_node','delivery'}<=stages,stages)
        replay=self.client.post('/api/dataops/research-questions',json=body)
        self.assertTrue(replay.json()['replayed']);self.assertEqual(replay.json()['workspace']['id'],workspace)
        again=self.client.get('/api/dataops/diagnostics/'+replay.headers['X-Request-ID']).json()
        self.assertNotIn('model_return',{e['stage'] for e in again['events']})
        self.assertEqual(self.client.get('/api/dataops/diagnostics/'+'f'*32).status_code,404)
        # A second cookie jar shares the running app, without a second lifespan.
        other=TestClient(self.app)
        try:
            self.assertEqual(other.get('/api/dataops/diagnostics/'+identifier).status_code,401)
            other.get('/dataops/research')
            self.assertEqual(other.get('/api/dataops/diagnostics/'+identifier).status_code,404)
        finally:other.close()

    def test_rejected_question_without_workspace_still_has_diagnostics(self):
        response=self.client.post('/api/dataops/research-questions',json={'question':'整理它们的功效','request_key':'no-object'})
        self.assertEqual(response.status_code,200);self.assertNotIn('workspace',response.json())
        receipt=self.client.get('/api/dataops/diagnostics/'+response.headers['X-Request-ID']).json()
        self.assertTrue(any(e['code']=='no_candidate_scope' for e in receipt['events']))
