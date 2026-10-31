"""Shared chat/workbench fallback and state-bound tool contracts, using fixtures."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from agents.conversation_store import ConversationStore
from tests.dataops_fixture import make_sources
from tests.test_research_question import draft
from tests.test_dataops_research_context import context
from tcm.dataops.research_question import definition,request_proposal
from tcm.dataops.research_context import affordances,action_rejection
from tcm.dataops.research_model import definitions


class Model:
    max_retries=0;max_tokens=1200;model_name='fixture'
    def __init__(self,args,tool='propose_research_goal',error=None):
        self.args,self.tool,self.error=args,tool,error;self.calls=0;self.bindings=[];self.messages=[]
        self.http_client=SimpleNamespace(close=lambda:None)
    def bind_tools(self,tools,**kw):self.bindings.append((tools,kw));return self
    def invoke(self,messages):
        self.calls+=1;self.messages.append(messages)
        if self.error:raise self.error
        return AIMessage(content='',tool_calls=[{'id':'fixture','name':self.tool,'args':self.args}])


class PolicyTests(unittest.TestCase):
    def test_late_same_key_admission_never_regresses_published_workspace(self):
        from tcm.dataops.workspace import Workspaces,Conflict
        with tempfile.TemporaryDirectory() as directory:
            store=Workspaces(directory)
            doc,_=store.reserve_agent('owner','key',{},'revision',question='question')
            store.project('owner',doc['id'],status='queue_busy',error='queue_busy')
            admitted=store.project('owner',doc['id'],job_id='job')
            self.assertEqual(admitted['status'],'building')
            store.project('owner',doc['id'],status='complete',result={'rows':[]})
            replay=store.project('owner',doc['id'],job_id='job')
            self.assertEqual(replay['status'],'complete')
            late=store.project('owner',doc['id'],status='queue_busy',error='queue_busy')
            self.assertEqual(late['status'],'complete')
            with self.assertRaises(Conflict):store.project('owner',doc['id'],job_id='another-job')
    def test_compact_schema_retains_all_query_actions_and_goal_contract(self):
        from tcm.dataops.research_question import understanding_input
        def normalized(v):
            if isinstance(v,list):return [normalized(x) for x in v]
            if isinstance(v,dict):return {k:normalized(x) for k,x in v.items() if k!='title'}
            return v
        for question,catalog,parent in [('这个平台适合做哪些工作',{},None),
              ('Alpha的靶点像什么',{'Alpha':['sid-a']},None)]:
            full=understanding_input(question,catalog,parent=parent,allow_queries=True,schema_profile='full_v1')
            compact=understanding_input(question,catalog,parent=parent,allow_queries=True)
            self.assertEqual(normalized(full[2]),normalized(compact[2]))
            self.assertLess(len(json.dumps(compact,ensure_ascii=False)),len(json.dumps(full,ensure_ascii=False))*.8)
        _,_,tools=understanding_input('给Alpha做功效表格',{'Alpha':['sid-a']},allow_queries=True)
        self.assertEqual([t['function']['name'] for t in tools],['propose_research_goal','submit_query_intent'])
        self.assertEqual(len(understanding_input('给Alpha做功效表格',{'Alpha':['sid-a']},allow_queries=True,schema_profile='compact_v1')[2]),1)
        # Mentioning a possible deliverable in a capability question does not hide query actions.
        self.assertEqual(len(understanding_input('能交付哪些表格',{},allow_queries=True)[2]),2)
    def test_goal_schema_exposes_nonempty_catalog_names_and_locked_parent(self):
        schema=definition({'Alpha':['sid-a'],'Beta':['sid-b']})['function']['parameters']
        names=schema['properties']['names']
        self.assertEqual(names['minItems'],1);self.assertEqual(names['items']['enum'],['Alpha','Beta'])
        self.assertEqual(definition({},parent={'id':'parent'})['function']['parameters']['properties']['names']['maxItems'],0)
        self.assertEqual(schema['properties']['operation']['enum'],['new'])
        parent=definition({},parent={'id':'parent'})['function']['parameters']['properties']
        self.assertEqual(parent['operation']['enum'],['append_fields']);self.assertEqual(parent['target_count'],{'type':'null'})

    def test_zero_partial_subset_named_and_parent_delivery_use_different_constraints(self):
        c=context();c['eligible']=[]
        props=next(t for t in definitions(c) if t['function']['name']=='deliver_result')['function']['parameters']['properties']
        self.assertEqual((props['source_ids']['minItems'],props['source_ids']['maxItems']),(0,0))
        self.assertEqual(props['allow_partial']['enum'],[True])
        self.assertIsNone(action_rejection(c,'deliver_result',{'source_ids':[],'allow_partial':True}))
        self.assertEqual(action_rejection(c,'deliver_result',{'source_ids':['A'],'allow_partial':True}),'unqualified_delivery_records')
        c['eligible']=['A']
        self.assertEqual(affordances(c)['deliverable_source_ids'],['A'])
        self.assertIsNone(action_rejection(c,'deliver_result',{'source_ids':['A'],'allow_partial':True}))
        self.assertEqual(action_rejection(c,'deliver_result',{'source_ids':[],'allow_partial':True}),'delivery_count_mismatch')
        for mode,parent in [('audit_named',[]),('select_complete',['A','B'])]:
            c.update(task_mode=mode,parent_records=parent,eligible=[])
            self.assertIsNone(action_rejection(c,'deliver_result',{'source_ids':['A','B'],'allow_partial':True}))
            self.assertIsNotNone(action_rejection(c,'deliver_result',{'source_ids':[],'allow_partial':True}))

    def test_one_model_can_route_query_but_cannot_drop_file_or_field_requirements(self):
        model=Model({'action':'capabilities'},tool='submit_query_intent')
        r=request_proposal(model,'能讲讲这套工具适用的工作种类与入口吗',{},authorized=True,allow_queries=True)
        self.assertEqual(r['status'],'query');self.assertEqual(r['route']['request']['topic'],'capabilities')
        self.assertEqual(model.calls,1);self.assertEqual(len(model.bindings[0][0]),2)
        # The actual SDK payload (including both schemas) must fit the existing transport budget.
        self.assertLess(len(json.dumps({'messages':model.messages[0],'tools':model.bindings[0][0]}).encode()),65536)
        from tcm.dataops.goal_model import GoalResponseRejected
        for question,args in [('Alpha的功效和贮藏',{'action':'research','entities':['Alpha'],'fields':['功能与主治']}),
                              ('给Alpha做功效表格',{'action':'herb_profile','entities':['Alpha']})]:
            with self.assertRaises(GoalResponseRejected):
                request_proposal(Model(args,'submit_query_intent'),question,{'Alpha':['sid-a']},authorized=True,allow_queries=True)


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        _,sources=make_sources(root/'sources');self.memory=ConversationStore(root/'memory.sqlite3')
        env={'ENABLE_DATAOPS':'1','ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'fixed',
             'ENABLE_DATAOPS_RESEARCH_GOAL_MODEL':'1','DATAOPS_ROOT':str(root/'work'),
             'RESEARCH_JOB_DB':str(root/'jobs.sqlite3'),'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':'',
             'ENABLE_NATURAL_ROUTING':'1','RESEARCH_TOOLS_ONLY':'1','ENABLE_INTENT_PLANNER':'0',
             'RAG_MODEL':'fixture','RAG_BASE_URL':'https://api.deepseek.com','RAG_API_KEY':'test-only'}
        p=patch.dict('os.environ',env);p.start();self.addCleanup(p.stop)
        import app
        self.app=app
        for p in (patch.object(app.agent_runtime,'memory',self.memory),patch('tcm.dataops.jobs.research_sources',sources),
                  patch('tcm.dataops.api.configured_sources',sources)):
            p.start();self.addCleanup(p.stop)
        self.client=TestClient(app.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.client.get('/conversation')
    def wait(self,id):
        for _ in range(200):
            d=self.client.get('/api/dataops/agent-runs/'+id).json()
            if d['job']['status'] not in ('queued','running'):return d['workspace']
            time.sleep(.02)
        self.fail('task did not finish')

    def test_local_complete_and_host_controls_never_construct_model(self):
        self.client.get('/dataops/research')
        self.assertIn('request-understanding-note',self.client.get('/').text)
        with patch('tcm.dataops.research_model.configured_model',side_effect=AssertionError('unneeded model')):
            for use_model in (None,False,True):
                body={'question':'整理Alpha和Beta的功效和注意，生成表格','request_key':'local-'+str(use_model),'use_model':use_model}
                r=self.client.post('/api/dataops/research-questions',json=body)
                self.assertEqual(r.status_code,202,r.text);d=self.wait(r.json()['workspace']['id'])
                self.assertEqual(d['goal_understanding']['provider_requests'],0)
            for i,q in enumerate(('Alpha的来源做成表','用Alpha预测疗效并生成表格')):
                r=self.client.post('/api/dataops/research-questions',json={'question':q,'request_key':'control-'+str(i)})
                self.assertEqual(r.status_code,200,r.text);self.assertNotIn('workspace',r.json())
                self.assertEqual(r.json()['proposal']['external_model_calls'],0)

    def test_unmarked_natural_task_runs_one_goal_and_same_owner_can_open_workbench(self):
        model=Model(draft(document_fields=['功能与主治','贮藏']))
        body={'query':'Alpha和Beta各占一行，主要用途、存放环境都给我列出来，缺什么标明，附每格出处','request_key':'unmarked-task'}
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            r=self.client.post('/chat',json=body);self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['model_calls'],1)
            identifier=r.json()['assistant']['workspace_id'];d=self.wait(identifier)
            self.assertEqual(len(d['result']['rows']),2);self.assertEqual(d['goal_understanding']['provider_requests'],1)
            self.assertEqual(model.calls,1)
            again=self.client.post('/chat',json=body)
            self.assertEqual(again.json()['assistant']['workspace_id'],identifier);self.assertEqual(model.calls,1)
            self.assertEqual(again.json()['model_calls'],0)
            self.assertEqual(again.json()['assistant']['llm_calls'],1)
            self.assertEqual(again.json()['assistant']['counts_scope'],'workspace_total')

    def test_query_handoff_uses_original_question_without_second_planner_and_replays_receipt(self):
        model=Model({'action':'capabilities'},tool='submit_query_intent')
        body={'query':'能讲讲这套工具适用的工作种类与入口吗','request_key':'query-handoff'}
        with patch('tcm.dataops.research_model.configured_model',return_value=model), \
             patch('tcm.intent_planner.plan_question',side_effect=AssertionError('second model planner')):
            r=self.client.post('/chat',json=body);self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['routing']['reason'],'unified_request_fallback')
            self.assertEqual(r.json()['model_calls'],1)
            second=self.client.post('/chat',json=body);self.assertEqual(second.status_code,200,second.text)
            self.assertEqual(second.json()['model_calls'],0);self.assertTrue(second.json()['routing']['understanding_replayed'])
            self.assertEqual(model.calls,1);self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'],[])

    def test_api_can_explicitly_disable_fallback_and_chat_cannot_drop_table(self):
        q='我正在准备Alpha和Beta的资料卡，请摘出功效和贮藏，缺失照实标注'
        self.client.get('/dataops/research')
        with patch('tcm.dataops.research_model.configured_model',side_effect=AssertionError('explicitly disabled')):
            r=self.client.post('/api/dataops/research-questions',json={'question':q,'request_key':'disabled-fallback','use_model':False})
            self.assertEqual(r.status_code,200,r.text);self.assertNotIn('workspace',r.json())
        model=Model({'action':'herb_profile','entities':['Alpha']},tool='submit_query_intent')
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            body={'query':'为Alpha做一份功效表格，原文没写就空着','request_key':'do-not-drop-table'}
            for attempt in range(2):
                r=self.client.post('/chat',json=body);self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(r.json()['assistant']['status'],'clarification_needed')
                self.assertEqual(r.json()['assistant']['llm_calls'],1)
                self.assertEqual(r.json()['model_calls'],1 if attempt==0 else 0)
            self.assertEqual(model.calls,1);self.assertEqual(self.client.get('/api/dataops/workspaces').json()['items'],[])

    def test_unknown_dispatch_keeps_cookie_owner_on_fresh_client_and_never_retries(self):
        model=Model({},error=TimeoutError('unknown'))
        fresh=TestClient(self.app.app);self.addCleanup(fresh.close)
        body={'query':'能讲讲这套工具适用的工作种类与入口吗','request_key':'unknown-chat-dispatch'}
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=fresh.post('/chat',json=body);self.assertEqual(first.status_code,503,first.text)
            self.assertTrue(fresh.cookies)
            second=fresh.post('/chat',json=body);self.assertEqual(second.status_code,409,second.text)
            self.assertEqual(model.calls,1)

    def test_busy_chat_same_key_retries_original_goal_without_model_or_failed_workspace(self):
        import hashlib
        cookie=self.client.cookies.get(self.app.SESSION_COOKIE_NAME)
        owner=hashlib.sha256(cookie.encode()).hexdigest();service=self.app.dataops_service
        queue=service.queue();blocker,_=queue.enqueue(owner,'hold queue','busy-blocker')
        body={'query':'Alpha和Beta各占一行，把主要用途与存放环境列出并附出处','request_key':'busy-chat'}
        model=Model(draft(document_fields=['功能与主治','贮藏']))
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/chat',json=body)
            self.assertEqual(first.status_code,429,first.text);self.assertEqual(first.headers['retry-after'],'2')
            saved=service.store().by_request(owner,body['request_key'])
            self.assertEqual(saved['status'],'queue_busy');self.assertFalse(saved.get('job_id'))
            self.assertEqual(saved['goal_understanding']['provider_requests'],1)
            second=self.client.post('/chat',json=body);self.assertEqual(second.status_code,429)
            self.assertEqual(model.calls,1)
            changed=self.client.post('/chat',json={**body,'query':'给Alpha生成表格'})
            self.assertEqual(changed.status_code,409)
            queue.cancel(owner,blocker['id'])
            retry=self.client.post('/chat',json=body);self.assertEqual(retry.status_code,200,retry.text)
            value=retry.json();self.assertEqual(value['assistant']['workspace_id'],saved['id'])
            self.assertEqual(value['model_calls'],0);self.assertEqual(value['assistant']['llm_calls'],1)
            self.assertEqual(value['assistant']['understanding_requests_this_submission'],0)
            self.assertNotIn('understanding_requests_this_submission',value)
            doc=self.wait(saved['id']);self.assertEqual(doc['status'],'complete')
            self.assertEqual(len(doc['result']['rows']),2);self.assertEqual(doc['goal'],saved['goal'])
            replay=self.client.post('/chat',json=body);self.assertEqual(replay.status_code,200)
            self.assertEqual(replay.json()['model_calls'],0);self.assertEqual(model.calls,1)
            self.assertEqual(len(service.store().listing(owner)),1)

    def test_busy_workbench_same_key_and_payload_reuses_paid_proposal(self):
        import hashlib
        self.client.get('/dataops/research')
        owner=hashlib.sha256(self.client.cookies.get(self.app.SESSION_COOKIE_NAME).encode()).hexdigest()
        service=self.app.dataops_service;queue=service.queue()
        blocker,_=queue.enqueue(owner,'hold queue','busy-workbench-blocker')
        body={'question':'Alpha和Beta各占一行，主要用途和存放环境各一列，交一张带出处的表格','request_key':'busy-api'}
        model=Model(draft(document_fields=['功能与主治','贮藏']))
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/api/dataops/research-questions',json=body)
            self.assertEqual(first.status_code,429,first.text)
            saved=service.store().by_request(owner,body['request_key'])
            self.assertEqual(saved['status'],'queue_busy')
            queue.cancel(owner,blocker['id'])
            retry=self.client.post('/api/dataops/research-questions',json=body)
            self.assertEqual(retry.status_code,202,retry.text);self.assertTrue(retry.json()['replayed'])
            self.assertEqual(retry.json()['workspace']['id'],saved['id'])
            self.assertEqual(self.wait(saved['id'])['status'],'complete');self.assertEqual(model.calls,1)

    def test_busy_retry_after_source_change_is_conflict_and_never_redrafts(self):
        import hashlib,sqlite3
        owner=hashlib.sha256(self.client.cookies.get(self.app.SESSION_COOKIE_NAME).encode()).hexdigest()
        service=self.app.dataops_service;queue=service.queue();blocker,_=queue.enqueue(owner,'hold queue','source-blocker')
        body={'query':'Alpha和Beta各放一行，主要用途与存放环境列出来，附出处','request_key':'busy-source-change'}
        model=Model(draft(document_fields=['功能与主治','贮藏']))
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            first=self.client.post('/chat',json=body);self.assertEqual(first.status_code,429,first.text)
            queue.cancel(owner,blocker['id'])
            with sqlite3.connect(Path(self.tmp.name)/'sources/enrichment.sqlite3') as db:
                db.execute('INSERT INTO metadata VALUES(?,?)',('test_revision','1'))
            retry=self.client.post('/chat',json=body)
            self.assertEqual(retry.status_code,409,retry.text);self.assertEqual(model.calls,1)
            self.assertEqual(service.store().by_request(owner,body['request_key'])['status'],'queue_busy')

    def test_unknown_enqueue_after_busy_stops_further_same_key_admission(self):
        import hashlib
        owner=hashlib.sha256(self.client.cookies.get(self.app.SESSION_COOKIE_NAME).encode()).hexdigest()
        service=self.app.dataops_service;queue=service.queue();blocker,_=queue.enqueue(owner,'hold queue','unknown-blocker')
        body={'query':'Alpha与Beta各放一行，主要用途与存放环境列出，附出处','request_key':'busy-then-unknown'}
        model=Model(draft(document_fields=['功能与主治','贮藏']))
        with patch('tcm.dataops.research_model.configured_model',return_value=model):
            self.assertEqual(self.client.post('/chat',json=body).status_code,429)
            queue.cancel(owner,blocker['id'])
            with patch('tcm.research_jobs.JobStore.enqueue',side_effect=OSError('unknown queue outcome')) as enqueue:
                retry=self.client.post('/chat',json=body);self.assertEqual(retry.status_code,503)
                saved=service.store().by_request(owner,body['request_key'])
                self.assertEqual(saved['status'],'failed');self.assertEqual(saved['error'],'queue_not_enqueued')
                replay=self.client.post('/chat',json=body);self.assertEqual(replay.status_code,200)
                self.assertEqual(replay.json()['assistant']['status'],'failed')
                self.assertEqual(replay.json()['assistant']['llm_calls'],1)
                self.assertEqual(enqueue.call_count,1);self.assertEqual(model.calls,1)


if __name__=='__main__':unittest.main()
