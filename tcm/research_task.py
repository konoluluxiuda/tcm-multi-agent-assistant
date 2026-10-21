"""Bounded native-tool workflow for synthetic source/target/literature verification.

The model selects actions; the server owns state, user decisions and evidence scope.
Only actions/observations are retained, never a model's free-form thought text.
"""
from copy import deepcopy
import json
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from tcm.research_task_fixtures import NAMES, QUESTION, SyntheticResearchTools
from tcm.research_task_store import ResearchTaskStore
from tcm.task_plan import TaskPlan, clarification, confirm_plan
from tcm.request_coverage import RequestScope,assess_coverage,require_covered_scope,cover_report
from tcm.task_request import draft_request,confirm_request,require_request_review


class Arguments(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class Resolve(Arguments):names:list[str]=Field(min_length=2,max_length=2)
class Common(Arguments):source_ids:list[str]=Field(min_length=2,max_length=2)
class Search(Arguments):
    target_id:str=Field(min_length=1,max_length=32)
    variant:Literal['initial','expanded']
class Inspect(Arguments):study_id:str=Field(min_length=1,max_length=64)
class Finish(Arguments):pass

SCHEMAS={'resolve_sources':Resolve,'common_targets':Common,'search_literature':Search,'inspect_study':Inspect,'finish_task':Finish}
DESCRIPTIONS={
 'resolve_sources':'Find fictional source candidates for exactly the two task herbs. The USER must choose sources.',
 'common_targets':'Read source-declared shared targets for the USER-confirmed source IDs. The USER then chooses targets.',
 'search_literature':'Search fictional records for a USER-selected target. If initial is empty, expanded is one bounded alternative; no real networking.',
 'inspect_study':'Inspect a returned fictional study for subject identity, retraction and provenance. Ingredient evidence cannot become herb evidence.',
 'finish_task':'Finish with a server-checked source table and gaps. Missing herb-level evidence means partial, not a fabricated complete answer.'}
TOOLS=[{'type':'function','function':{'name':name,'description':DESCRIPTIONS[name],'parameters':schema.model_json_schema()}}
       for name,schema in SCHEMAS.items()]
TERMINAL={'complete','partial','budget_exhausted','failed','interrupted'}
SYSTEM='''你是合成资料科研任务的工具调度器。只通过提供的工具行动，每次仅调用一个工具，不输出思维链或自由回答。
先resolve_sources，等用户确认后common_targets，等用户选靶点后检索。initial为空可用expanded补检一次。
对搜索返回的每条记录调用inspect_study，区分药材本体、成分、对象不同及撤稿。不能根据成分研究推断药材疗效。
只使用当前任务里已确认的实体及返回的记录ID。不可替用户选来源或靶点，工具报错时据观察修正或结束。
无外部网络、药典或ETCM工具；全部内容虚构。最后finish_task，由服务器生成证据表与缺口，不编写医学结论。'''


def public_state(task):
    return {k:task[k] for k in ('data_mode','question','names','sources','targets','relations','searches','studies','events')}


class NativeToolModel:
    """Uses the installed model SDK's actual bind_tools/tool_calls protocol."""
    def __init__(self, model, *, live_provider=False):
        self.live_provider=live_provider
        self.model=model.bind_tools(TOOLS,tool_choice='auto',parallel_tool_calls=False)

    def decide(self, task):
        from langchain_core.messages import SystemMessage,HumanMessage,AIMessage,ToolMessage
        context={k:v for k,v in public_state(task).items() if k!='events'}
        messages=[SystemMessage(content=SYSTEM),HumanMessage(content=json.dumps(context,ensure_ascii=False))]
        events=iter(task['events'])
        groups=[]
        for event in events:
            if (event['kind']=='tool' and groups and isinstance(groups[-1],list) and
                    event.get('model_step') is not None and groups[-1][0].get('model_step')==event['model_step']):
                groups[-1].append(event)
            else:groups.append([event] if event['kind']=='tool' else event)
        for group in groups:
            if isinstance(group,list):
                messages.append(AIMessage(content='',tool_calls=[{'name':e['name'],'args':e['args'],'id':e['call_id'],'type':'tool_call'} for e in group]))
                for e in group:
                    messages.append(ToolMessage(content=json.dumps(e['observation'],ensure_ascii=False),tool_call_id=e['call_id']))
            elif group['kind']=='user_selection':
                messages.append(HumanMessage(content='用户已明确选择：'+json.dumps(group['selection'],ensure_ascii=False)))
        if sum(len(str(m.content)) for m in messages)>24000:raise ValueError('Context budget exhausted')
        reply=self.model.invoke(messages)
        calls=reply.tool_calls
        if getattr(reply,'invalid_tool_calls',[]) or not 1<=len(calls)<=2:
            raise ValueError('One action or two independent study inspections required')
        if len(calls)==2 and any(c['name']!='inspect_study' for c in calls):
            raise ValueError('Multiple prerequisite-changing actions are not allowed')
        call=calls[0]
        result={'name':call['name'],'args':call['args'],'call_id':call['id'],
                'usage':getattr(reply,'usage_metadata',None)}
        if len(calls)==2:result['native_batch']=calls
        return result



class ScriptedToolModel:
    """Deterministic test driver, not LLM reasoning or measured model performance."""
    def __init__(self, adaptive=True):self.adaptive=adaptive
    def decide(self, task):
        if not task['sources']:name,args='resolve_sources',{'names':task['names']}
        elif not task['relations']:name,args='common_targets',{'source_ids':task['sources']}
        elif task.get('plan',{}).get('intent')=='source_relations':name,args='finish_task',{}
        else:
            for target in task['targets']:
                searches=[r for r in task['searches'] if r['target_id']==target]
                if not searches:return {'name':'search_literature','args':{'target_id':target,'variant':'initial'}}
                if self.adaptive and searches[-1]['status']=='empty' and len(searches)==1:
                    return {'name':'search_literature','args':{'target_id':target,'variant':'expanded'}}
                for search in searches:
                    for row in search['records']:
                        if row['id'] not in task['studies']:
                            return {'name':'inspect_study','args':{'study_id':row['id']}}
            name,args='finish_task',{}
        return {'name':name,'args':args}


def report(task):
    gaps=[]
    if not task['sources']:gaps.append('尚未确认两份来源。')
    relation_only=task.get('plan',{}).get('intent')=='source_relations'
    empty=relation_only and task.get('common_query_done') and not task['relations']
    if not task['relations'] and not empty:gaps.append('尚无共同靶点来源记录。')
    if not task['targets'] and not empty:gaps.append('尚未由用户选择目标靶点。')
    if relation_only:
        return cover_report(task,{'data_mode':'synthetic_only','complete':not gaps,'source_ids':task['sources'],
            'selected_targets':task['targets'],'relation_evidence':[r for r in task['relations'] if r['id'] in task['targets']],'study_evidence':[],
            'gaps':gaps,'plan':task['plan'],'task_scope':'source_relations',
            'research_evidence_verified':False,'outcome':'empty' if empty else 'matched' if not gaps else 'incomplete',
            'limitations':['仅完成合成来源关系任务，未检索研究记录，不证明真实关系或疗效。'],
            'notice':'全为虚构软件测试资料；按用户选择仅核对来源关系，不执行合成研究记录检索。'})
    evidence=[]
    for target in task['targets']:
        searches=[s for s in task['searches'] if s['target_id']==target]
        if not searches:gaps.append(target+'尚未检索。')
        seen={r['id'] for s in searches for r in s['records']}
        if seen-set(task['studies']):gaps.append(target+'有返回记录尚未核查。')
        accepted=[s for s in task['studies'].values() if s['target_id']==target and s['classification']=='herb_source_record']
        if not accepted:gaps.append(target+'没有可用的药材本体合成证据；成分记录不能补齐。')
    for s in task['studies'].values():
        evidence.append({k:s[k] for k in ('id','target_id','evidence_id','classification','source_locator','text')})
    return cover_report(task,{'data_mode':'synthetic_only','complete':not gaps,'source_ids':task['sources'],'selected_targets':task['targets'],
            'relation_evidence':task['relations'],'study_evidence':evidence,'gaps':gaps,
            'notice':'全为虚构软件测试资料；来源关系不证明机制，成分研究不等于药材本体研究。未做真实语义/临床有效性验收。'})


class ResearchTaskEngine:
    def __init__(self, store, model, tools):
        if not isinstance(tools,SyntheticResearchTools):raise ValueError('This version only accepts synthetic tools')
        self.store,self.model,self.tools=store,model,tools

    def create(self, *, max_model_calls=12,max_tool_calls=12,max_active_ms=120000,owner=None,plan=None,
               review_plan=False,plan_origin=None,request_scope=None,question=None):
        if (any(type(v) is not int for v in (max_model_calls,max_tool_calls,max_active_ms)) or
                not 1<=max_model_calls<=12 or not 1<=max_tool_calls<=12 or not 1<=max_active_ms<=120000):
            raise ValueError('Invalid task budget')
        if plan is not None:plan=TaskPlan.model_validate(plan)
        if request_scope is not None:
            if plan is None:raise ValueError('Declared requests require a plan')
            request_scope=RequestScope.model_validate(request_scope)
        if question is not None and (plan is not None or request_scope is not None or review_plan):
            raise ValueError('Question review cannot bypass inventory confirmation')
        task={'owner':owner,'data_mode':'synthetic_only','fixture_version':self.tools.version,'fixture_sha256':self.tools.fingerprint,'profile':self.tools.profile,
            'question':QUESTION,'names':NAMES[:],'status':'running','pending':None,'sources':[],'targets':[],
            'relations':[],'searches':[],'studies':{},'events':[],'model_calls':0,'provider_attempt_slots':0,'tool_calls':0,'active_ms':0,
            'max_model_calls':max_model_calls,'max_tool_calls':max_tool_calls,'max_active_ms':max_active_ms,
            'inflight':None,'invalid_actions':0,'report':None}
        if plan is not None:
            task.update(plan=plan.model_dump(),names=plan.names(),
                        question='确认合成任务范围后，按计划核对来源、所选靶点及要求的记录。')
            if request_scope is not None:
                task['request_scope']=request_scope.model_dump()
                coverage=assess_coverage(task)
                if coverage['needs_reduction'] or coverage['extra_entities'] or coverage['extra_deliverables']:
                    review_plan=True
            if review_plan:
                task.update(plan_review_required=True,proposed_plan=plan.model_dump(),plan_origin=plan_origin)
            if plan.missing() or review_plan:task.update(status='waiting_plan',pending=clarification(plan,review=review_plan))
        if question is not None:
            draft=draft_request(question)
            task.update(question=question,request_draft=draft,plan=TaskPlan().model_dump(),names=[],
                        status='waiting_request',pending={'kind':'request'})
        return self.store.create(task)

    def review_request(self,identifier,scope,*,revision,confirm_complete):
        from tcm.research_task_store import TaskConflict
        task=self.store.get(identifier)
        if task['revision']!=revision:raise TaskConflict('Task changed')
        if task.get('data_mode')!='synthetic_only' or 'request_draft' not in task:
            raise ValueError('Not a synthetic question review task')
        confirm_request(task,scope,confirm_complete=confirm_complete)
        return self.store.save(task)

    def choose(self, identifier, selection, *, revision,accept_reduced_scope=False):
        t=self.store.get(identifier)
        if t['revision']!=revision:
            from tcm.research_task_store import TaskConflict
            raise TaskConflict('Task changed; reload options')
        pending=t.get('pending')
        if not pending:raise ValueError('No pending user selection')
        if pending['kind']=='request':raise ValueError('Review the request inventory first')
        require_request_review(t)
        if pending['kind']=='plan':
            confirm_plan(t,selection,accept_reduced_scope=accept_reduced_scope)
            t['events'].append({'kind':'user_selection','selection':deepcopy(selection),'phase':'plan'})
            if t.get('request_scope'):t['events'][-1]['scope_acceptance']=deepcopy(t['scope_acceptance'])
            return self.store.save(t)
        if accept_reduced_scope:raise ValueError('Scope acceptance is only allowed during plan review')
        require_covered_scope(t)
        if pending['kind']=='sources':
            if not isinstance(selection,dict) or set(selection)!=set(t['names']):raise ValueError('Select every named source')
            for group in pending['options']:
                if selection[group['name']] not in {o['id'] for o in group['options']}:raise ValueError('Source not offered')
            t['sources']=[selection[n] for n in t['names']]
        else:
            if (not isinstance(selection,list) or not 1<=len(selection)<=2 or len(set(selection))!=len(selection)
                    or not set(selection)<={r['id'] for r in pending['options']}):raise ValueError('Choose one or two offered targets')
            t['targets']=selection[:]
        t['events'].append({'kind':'user_selection','selection':deepcopy(selection)})
        t.update(pending=None,status='running')
        return self.store.save(t)

    def retry_interrupted(self, identifier, *, revision):
        t=self.store.get(identifier)
        if t['revision']!=revision:
            from tcm.research_task_store import TaskConflict
            raise TaskConflict('Task changed')
        if t['status']!='interrupted':raise ValueError('Task is not interrupted')
        t['events'].append({'kind':'recovery','note':'Explicit retry of interrupted read-only step; reserved budgets remain consumed.'})
        t.update(status='running',inflight=None,report=None)
        return self.store.save(t)

    def retry_failed_model_step(self, identifier, *, revision):
        t=self.store.get(identifier)
        if t['revision']!=revision:
            from tcm.research_task_store import TaskConflict
            raise TaskConflict('Task changed')
        if t['status']!='failed' or t.get('failure',{}).get('phase')!='model':
            raise ValueError('Only a failed model/protocol step can be explicitly retried')
        t['events'].append({'kind':'recovery','note':'Explicit model-step retry; all reserved budgets retained.',
                            'previous_failure':t.pop('failure')})
        t.update(status='running',inflight=None,report=None)
        return self.store.save(t)

    def execute(self, task, name, args):
        if name not in SCHEMAS:raise ValueError('Unknown tool')
        require_request_review(task)
        require_covered_scope(task)
        if task.get('plan'):
            plan=TaskPlan.model_validate(task['plan'])
            if plan.missing() or task.get('plan_review_required') or name not in plan.tools():raise ValueError('Tool outside confirmed plan')
        args=SCHEMAS[name].model_validate(args).model_dump()
        signature=json.dumps([name,args],sort_keys=True,ensure_ascii=False)
        if any(e.get('signature')==signature for e in task['events']):raise ValueError('Duplicate action')
        if name=='resolve_sources':
            if args['names']!=task['names'] or task['sources']:raise ValueError('Names differ from task or sources already selected')
            options=self.tools.sources(args['names']);task.update(status='waiting_sources',pending={'kind':'sources','options':options})
            return {'status':'user_selection_required','options':options}
        if name=='common_targets':
            if not task['sources'] or args['source_ids']!=task['sources']:raise ValueError('Use exactly user-selected sources')
            rows=self.tools.common(args['source_ids']);task['relations']=rows
            task['common_query_done']=True
            if not rows:
                task['report']=report(task);task['status']='complete' if task['report']['complete'] else 'partial'
                return {'status':'no_common_targets','rows':[]}
            task.update(status='waiting_targets',pending={'kind':'targets','options':rows})
            return {'status':'user_selection_required','options':rows}
        if name=='search_literature':
            if args['target_id'] not in task['targets']:raise ValueError('Target not selected by user')
            try:result=self.tools.search(args['target_id'],args['variant'])
            except TimeoutError:result={'status':'unavailable','records':[],'reason':'synthetic_tool_timeout'}
            row={**args,**result};task['searches'].append(row)
            return row
        if name=='inspect_study':
            if args['study_id'] not in {r['id'] for s in task['searches'] for r in s['records']}:
                raise ValueError('Study was not returned by this task')
            s=self.tools.study(args['study_id'])
            scope=s.get('subject_scope_override') or s['subject_scope']
            s['classification']=('excluded_wrong_target' if s['target_id'] not in task['targets'] else
                'excluded_retracted' if s['retracted'] else
                'excluded_wrong_object' if set(s['herb_source_ids'])!=set(task['sources']) else
                'ingredient_only' if scope=='ingredient' else 'herb_source_record')
            task['studies'][s['id']]=s
            return s
        task['report']=report(task);task['status']='complete' if task['report']['complete'] else 'partial'
        return task['report']

    def run(self, identifier):
        t=self.store.get(identifier)
        if (t['profile']!=self.tools.profile or t['fixture_version']!=self.tools.version or
                t['fixture_sha256']!=self.tools.fingerprint):raise ValueError('Fixture binding changed')
        if t['status']=='running':
            require_request_review(t)
            require_covered_scope(t)
        if t['inflight']:
            t.update(status='interrupted',report=report(t));return self.store.save(t)
        while t['status']=='running':
            if (t['model_calls']>=t['max_model_calls'] or t['tool_calls']>=t['max_tool_calls'] or t['active_ms']>=t['max_active_ms']):
                t.update(status='budget_exhausted',report=report(t));self.store.save(t);break
            t['model_calls']+=1
            if getattr(self.model,'live_provider',False):t['provider_attempt_slots']+=1
            t['inflight']={'kind':'model'};self.store.save(t)
            start=time.monotonic()
            try:
                decision=self.model.decide(deepcopy(t))
                if (not isinstance(decision,dict) or not isinstance(decision.get('name'),str) or
                        len(decision['name'])>64 or not isinstance(decision.get('args'),dict) or
                        len(json.dumps(decision['args'],ensure_ascii=False))>2000):
                    raise ValueError('Invalid decision envelope')
            except Exception as error:
                t['failure']={'phase':'model','error_type':type(error).__name__}
                t['active_ms']+=round((time.monotonic()-start)*1000,3)
                t.update(status='failed',inflight=None,report=report(t));self.store.save(t);break
            t['active_ms']+=round((time.monotonic()-start)*1000,3)
            if t['active_ms']>=t['max_active_ms']:
                t.update(status='budget_exhausted',inflight=None,report=report(t));self.store.save(t);break
            actions=decision.get('native_batch') or [decision]
            if len(actions)>1:
                try:
                    allowed={r['id'] for q in t['searches'] for r in q['records']}
                    ids=[Inspect.model_validate(a['args']).study_id for a in actions]
                    if (len(actions)!=2 or any(a['name']!='inspect_study' for a in actions) or
                            len(set(ids))!=2 or not set(ids)<=allowed or set(ids)&set(t['studies'])):
                        raise ValueError('Batch inspections are not independent offered records')
                except Exception:
                    t.update(status='failed',inflight=None,report=report(t),failure={'phase':'model','error_type':'InvalidInspectionBatch'})
                    self.store.save(t);break
            if t['tool_calls']+len(actions)>t['max_tool_calls']:
                t.update(status='budget_exhausted',inflight=None,report=report(t));self.store.save(t);break
            for index,action in enumerate(actions):
                name,args=action.get('name'),action.get('args')
                t['tool_calls']+=1;t['inflight']={'kind':'tool','name':name};self.store.save(t)
                draft=deepcopy(t);stamp=time.monotonic()
                try:observation=self.execute(draft,name,args)
                except TimeoutError:observation={'status':'unavailable','reason':'synthetic_tool_timeout'}
                except Exception:
                    draft=deepcopy(t);draft['invalid_actions']+=1
                    observation={'status':'invalid_action','reason':'Tool or arguments violate this task scope'}
                    if draft['invalid_actions']>=2:draft.update(status='failed',report=report(draft))
                draft['active_ms']+=round((time.monotonic()-stamp)*1000,3)
                draft['events'].append({'kind':'tool','name':name,'args':args,'call_id':action.get('call_id') or action.get('id') or 'call-'+str(draft['tool_calls']),
                    'model_step':draft['model_calls'],
                    'signature':json.dumps([name,args],sort_keys=True,ensure_ascii=False),'observation':observation,
                    'duration_ms':round((time.monotonic()-stamp)*1000,3),'usage':decision.get('usage') if index==0 else None})
                draft['inflight']=None;self.store.save(draft);t=draft
                if t['status']!='running':break
        return t
