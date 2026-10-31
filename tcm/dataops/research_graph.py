"""R4 selection profile reusing D2 checkpoint, dispatch, interrupt and lease fences.

Only the decision/tool policy changes. Legacy Engine and its saved goals remain
unchanged; publication/verification is one host-owned delivery transaction.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from tcm.dataops.graph import Engine,Limits,ToolRejected,evidence_items,_parent_inspections,difference,projection
from tcm.dataops.baseline import collect,validate_result
from tcm.dataops.artifacts import publish,verify
from tcm.dataops.data_access import canonical,fingerprint
from tcm.dataops.research_contracts import ResearchGoal,same_research_scope
from tcm.dataops.research_model import valid
from tcm.dataops.research_context import affordances,action_rejection,bounded_context,history_item,search_key,HISTORY_PAIRS
from tcm.dataops.contracts import LABELS
from tcm.dataops.workspace import Conflict


class ResearchLimits(Limits):
    model_calls=10


class ResearchEngine(Engine):
    def __init__(self,*args,**kwargs):
        kwargs.setdefault('limits',ResearchLimits())
        super().__init__(*args,**kwargs)
        self.goal=ResearchGoal.model_validate(self.doc['goal'])
        files=('research_graph.py','research_model.py','research_sources.py','research_contracts.py','research_context.py')
        self.implementation_revision=hashlib.sha256(canonical({'base':self.implementation_revision,
            'profile':{n:fingerprint(Path(__file__).with_name(n)) for n in files},
            'retrieval':fingerprint(Path(__file__).parents[1]/'rag_retrieval.py')}).encode()).hexdigest()

    def eligible(self,s,sid):
        inspected=s['inspections'].get(sid)
        if not inspected or len(inspected['candidates'])!=1 or inspected['status'] in ('unmatched','source_mismatch','ambiguous'):
            return False
        record=next(r for r in s['selected']['records'] if r['source_record_id']==sid)
        if any(record['fields'][f].get('state')!='present' for f in self.goal.attributes):return False
        allowed={'candidate_value'} if self.goal.research.monograph_only else {'candidate_value','candidate_scope_value'}
        return all(inspected['fields'].get(f,{}).get('status') in allowed for f in self.goal.document_fields)

    def context(self,s):
        selected=s['selected'];ids=selected['selected_ids'] if selected else []
        unread=[sid for sid in ids if sid not in s['inspections'] or
                s['inspections'][sid]['status']=='candidate_selected_pending' or
                s['inspections'][sid]['status'] not in ('unmatched','source_mismatch','ambiguous') and
                any(f not in s['inspections'][sid]['fields'] for f in self.goal.document_fields)]
        eligible=[sid for sid in ids if self.eligible(s,sid)]
        evidence={**evidence_items(s),**s.get('research_evidence',{})}
        observation=deepcopy(s.get('observation'))
        if observation and 'fragments' in observation:observation['fragments']=observation['fragments'][:3]
        parent_records=s.get('parent_records',[])
        context={'original_question':s['question'],'goal':s['goal'],'listed':selected is not None,
                'candidates':[{'source_id':r['source_record_id'],'name':r['fields']['name_pinyin']['value']}
                              for r in selected['records']] if selected else [],
                'unread':unread,'eligible':eligible,'target_count':self.goal.research.target_count,
                'task_mode':self.goal.research.mode,
                'parent_records':parent_records,
                'inspections':[{'source_id':sid,'mapping_status':x['status'],
                    'attribute_states':{f:next(r for r in selected['records'] if r['source_record_id']==sid)['fields'][f]['state'] for f in self.goal.attributes},
                    'field_states':{f:c['status'] for f,c in x['fields'].items()},
                    'candidates':[{'entry_id':c['entry_id'],'name_zh':c['name_zh']} for c in x['candidates']]}
                    for sid,x in s['inspections'].items()],
                'evidence_refs':sorted(evidence)[:60],'evidence_already_read':s.get('evidence_reads',[]),
                'observation':observation,'decision_slots_remaining_including_current':max(0,self.limits.model_calls-s['model_calls']),
                'tool_actions_remaining':max(0,self.limits.tool_calls-s['tool_calls']),
                'source_reads_remaining':max(0,self.limits.leaf_reads-s['leaf_reads']),
                'fixed_plan':s.get('fixed_plan'), 'fixed_plan_cursor':s.get('fixed_plan_cursor',0),
                'decided_source_ids':sorted(s['decisions']),
                'completed_searches':s.get('completed_searches',[]),
                'completed_tool_actions':s['tool_calls'],
                'recent_actions':s.get('tool_history',[])[-HISTORY_PAIRS:],
                'progress_watch':{'consecutive_no_progress':s['stalls'],'stop_after':2,
                    'last_action_made_progress':s.get('observation',{}).get('made_progress') if s.get('observation') else None},
                'delivery_is_host_owned':True,'candidate_identity_verified':False}
        context['available_actions']=affordances(context)
        return bounded_context(context,self.limits.context_bytes)

    def decide(self,s):
        s['action']=None
        if s['model_calls']>=self.limits.model_calls:s.update(status='partial',reason='model_budget');return
        if s.get('fixed_plan') and s['fixed_plan_cursor']>=len(s['fixed_plan']):
            s.update(status='partial',reason='fixed_plan_exhausted');return
        context=self.context(s)
        if len(canonical(context).encode())>self.limits.context_bytes:s.update(status='partial',reason='context_budget');return
        action,unknown=self._receipt(s['model_calls']+1,context);s['model_calls']+=1
        if unknown:s.update(status='interrupted',reason='model_dispatch_unknown');return
        if not isinstance(action,dict) or '_rejected_response' in action:
            s.update(status='partial',reason='model_response_rejected');return
        if set(action)-{'tool','arguments','usage','fixed_plan'} or not valid(action.get('tool'),action.get('arguments')):
            s.update(status='partial',reason='invalid_tool_arguments');return
        if 'fixed_plan' in action:
            if s['driver']!='research_once_plan_v1' or s.get('fixed_plan'):
                s.update(status='partial',reason='invalid_plan');return
            s['fixed_plan']=action['fixed_plan'];s['fixed_plan_cursor']=1
        elif s.get('fixed_plan'):
            s['fixed_plan_cursor']+=1
        s['action']=action;s['usage'].append(action.get('usage',{}))

    def tools(self,s):
        if s['tool_calls']>=self.limits.tool_calls:s.update(status='partial',reason='tool_budget');return
        action=s['action'];tool,args=action['tool'],action['arguments'];s['tool_calls']+=1
        s['plan'].append({'id':'step-'+str(s['tool_calls']),'tool':tool,'purpose':args['purpose'],'status':'executed'})
        before=canonical([s['selected'],s['inspections'],s.get('research_evidence'),s.get('evidence_reads'),s.get('artifact'),s['decisions'],s.get('pending')])
        obs={'tool':tool,'status':'ok'}
        def reads(n):
            if s['leaf_reads']+n>self.limits.leaf_reads:raise ToolRejected('source_read_budget')
            s['leaf_reads']+=n;self.guard()
        try:
            rejected=action_rejection(self.context(s),tool,args)
            if rejected:raise ToolRejected(rejected)
            with self.sources_factory() as sources:
                if sources.revision!=s['source_revision']:raise Conflict('Source version changed')
                if tool=='list_candidates':
                    if s['selected'] is None:
                        reads(1);selected=sources.query(self.goal.query)
                        parent=self.store.get(self.owner,self.doc['parent_id']) if self.doc.get('parent_id') else None
                        if parent:
                            validate_result(parent['result'])
                            # Incremental work preserves the parent row set, never silently substitutes other herbs.
                            if parent['goal']['query']!=self.doc['goal']['query']:raise Conflict('Parent candidate scope changed')
                            if not same_research_scope(parent['goal'],self.doc['goal']):
                                raise Conflict('A revision must preserve parent requirements')
                            ids=[r['source_id'] for r in parent['result']['rows']]
                            if set(ids)-set(selected['selected_ids']):raise Conflict('Parent records no longer in scope')
                            selected={**selected,'selected_ids':sorted(ids),'records':[r for r in selected['records'] if r['source_record_id'] in ids],
                                      'returned':len(ids)}
                            s['parent_records']=ids
                        s['selected']=selected
                        s['inspections'],s['reused_cells']=_parent_inspections(parent,self.goal.model_dump(),selected['selected_ids'],self.doc.get('refresh_source_ids',[]))
                        if parent:s['decisions'].update(parent['result'].get('decisions',{}))
                    obs.update(returned=s['selected']['returned'],unknown_names=s['selected']['unknown_names'],reused_cells=s.get('reused_cells',0))
                elif s['selected'] is None:raise ToolRejected('list_candidates_required')
                elif tool in ('inspect_candidates','search_documents','deliver_result'):
                    ids=args['source_ids']
                    if set(ids)-set(s['selected']['selected_ids']):raise ToolRejected('outside_candidate_scope')
                    records={r['source_record_id']:r for r in s['selected']['records']}
                    if tool=='inspect_candidates':
                        changed=[]
                        for sid in ids:
                            old=s['inspections'].get(sid)
                            if old and old['status'] in ('unmatched','source_mismatch','ambiguous'):continue
                            fields=[f for f in self.goal.document_fields if not old or f not in old['fields']]
                            if old and not fields and old['status']!='candidate_selected_pending':continue
                            reads(1);current=sources.inspect(records[sid],fields,candidate_id=s['decisions'].get(sid) or None)
                            if old:current['fields']={**old['fields'],**current['fields']}
                            s['inspections'][sid]=current;changed.append(sid)
                        obs['inspected']=changed
                    elif tool=='search_documents':
                        names=[];owners={}
                        for sid in ids:
                            x=s['inspections'].get(sid)
                            if not x or len(x['candidates'])!=1 or x['status'] in ('unmatched','source_mismatch','ambiguous'):
                                raise ToolRejected('inspect_unique_candidates_first')
                            c=x['candidates'][0];names.append(c['name_zh']);owners[c['entry_id']]=sid
                        reads(1)
                        try:
                            result=sources.search.search(args['query'],'bm25',6,names=names,
                                                         fields=[LABELS[f] for f in self.goal.document_fields])
                        except ValueError as exc:
                            raise ToolRejected('search_outside_goal_or_unresolved_query') from exc
                        refs=[]
                        for hit in result['hits']:
                            if hit['entry_id'] not in owners:raise Conflict('Cross-object evidence')
                            item={'source_id':owners[hit['entry_id']],'kind':'pharmacopoeia',
                                  'source':{k:hit[k] for k in ('chunk_id','entry_id','label','scope','text')}}
                            item['source']['payload']={'evidence':hit['evidence']}
                            ref='E-'+hashlib.sha256(canonical(item).encode()).hexdigest()[:24]
                            s.setdefault('research_evidence',{})[ref]=item;refs.append(ref)
                        obs.update(search_status=result['status'],evidence_ids=refs,
                                   warning='Search does not fill a missing independent field or approve identity')
                        s.setdefault('completed_searches',[]).append(search_key(ids,args['query']))
                    else:
                        if len(ids)>self.goal.research.target_count:raise ToolRejected('too_many_delivery_records')
                        audit_named=self.goal.research.mode=='audit_named'
                        if audit_named and set(ids)!=set(s['selected']['selected_ids']):raise ToolRejected('preserve_named_records')
                        if s.get('parent_records') and set(ids)!=set(s['parent_records']):raise ToolRejected('preserve_parent_records')
                        qualified=[sid for sid in ids if self.eligible(s,sid)]
                        full=len(ids)==self.goal.research.target_count and len(qualified)==len(ids)
                        if audit_named:
                            full=(len(ids)==self.goal.research.target_count and all(sid in s['inspections'] for sid in ids)
                                  and not self.context(s)['unread'] and not s['selected']['unknown_names'] and not s['selected']['unknown_source_ids'])
                        if not full and not args['allow_partial']:raise ToolRejected('target_or_fields_incomplete')
                        if any(sid not in s['inspections'] for sid in ids):raise ToolRejected('inspect_before_delivery')
                        picked={**s['selected'],'selected_ids':sorted(ids),'records':[records[sid] for sid in ids],'returned':len(ids)}
                        result=collect(sources,self.goal.legacy().model_dump(),selected=picked,inspections=s['inspections'])
                        result.update(status='complete' if full else 'partial',mode='research_selection',execution_label='R4 / '+s['driver'],
                                      model_calls=s['model_calls'],tool_calls=s['tool_calls'],leaf_reads=s['leaf_reads'],
                                      decisions=s['decisions'],usage=s['usage'],trace=s['events']+[obs])
                        result['evidence'].update(s.get('research_evidence',{}))
                        result['research']={'target_count':self.goal.research.target_count,'qualified_count':len(qualified),
                            'task_mode':self.goal.research.mode,'reviewed_count':len(ids),
                            'artifact_format':'sorted-evidence-v1',
                            'candidate_pool_count':len(s['selected']['selected_ids']),'selected_ids':ids,
                            'candidate_states':[{'source_id':sid,'status':s['inspections'].get(sid,{}).get('status','not_inspected'),
                                'eligible':self.eligible(s,sid),'selected':sid in ids} for sid in s['selected']['selected_ids']],
                            'decision_steps':s['model_calls'],
                            'provider_requests':sum(u.get('provider_requests',0) for u in s['usage']),
                            'identity_verified':False,'exhaustive_search':False,'monograph_only':self.goal.research.monograph_only}
                        if self.doc.get('parent_id'):
                            result['diff']=difference(self.store.get(self.owner,self.doc['parent_id'])['result'],result)
                        validate_result(result);self.guard();sources.assert_unchanged()
                        s['artifact']=publish(self.directory,result,version=self.doc['revision'],reconcile=True)
                        verify(self.directory/s['artifact']['filename'],result)
                        s.update(result=result,verified=True,status=result['status'])
                        obs.update(delivered=len(ids),qualified=len(qualified),host_verified=True)
                elif tool=='read_evidence':
                    evidence={**evidence_items(s),**s.get('research_evidence',{})};refs=args['evidence_ids']
                    if any(ref not in evidence for ref in refs):raise ToolRejected('unknown_evidence_handle')
                    reads(len(refs));obs['fragments']=[]
                    for ref in refs:
                        text=str(evidence[ref]['source'].get('text',evidence[ref]['source'].get('raw_value','')))
                        obs['fragments'].append({'evidence_id':ref,'text':text[:800],'truncated':len(text)>800})
                    s['evidence_reads']=sorted(set(s.get('evidence_reads',[]))|set(refs))
                elif tool=='ask_user':
                    from uuid import uuid4
                    sid=args['source_id'];x=s['inspections'].get(sid)
                    if not x or x['status']!='ambiguous' or sid in s['decisions']:raise ToolRejected('no_source_ambiguity')
                    s.update(status='waiting_input',pending={'question_id':uuid4().hex,'revision':self.doc['revision'],
                             'source_id':sid,'candidates':x['candidates'],'operations':['choose','skip','cancel']})
                sources.assert_unchanged()
        except ToolRejected as exc:obs.update(status='rejected',reason=str(exc))
        except TimeoutError:obs.update(status='read_timeout');s.update(status='partial',reason='local_read_timeout')
        after=canonical([s['selected'],s['inspections'],s.get('research_evidence'),s.get('evidence_reads'),s.get('artifact'),s['decisions'],s.get('pending')])
        obs['made_progress']=before!=after
        s['stalls']=s['stalls']+1 if before==after and s['status']=='running' else 0
        obs['consecutive_no_progress']=s['stalls']
        s['observation']=obs;s['events'].append(obs)
        s['plan'][-1]['status']=('rejected' if obs['status']=='rejected' else 'waiting_input' if s['status']=='waiting_input'
                                else 'no_progress' if not obs['made_progress'] else 'executed')
        s.setdefault('tool_history',[]).append(history_item(s['tool_calls'],tool,args,obs))
        s['tool_history']=s['tool_history'][-HISTORY_PAIRS:]
        if s['stalls']>=2:s.update(status='partial',reason='no_progress')

    def _project(self,state):
        progress=projection(state)
        progress['research']={'target_count':self.goal.research.target_count,'eligible':self.context(state)['eligible'],
                              'decision_steps':state['model_calls'],'delivery_is_host_owned':True}
        return self.store.project(self.owner,self.identifier,status=state['status'],pending=state.get('pending'),
            progress=progress,result=state.get('result'),artifact=state.get('artifact'),error=state.get('reason'))
