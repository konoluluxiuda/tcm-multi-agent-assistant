"""State-derived tool affordances and bounded, reconstructable tool history.

This policy constrains legal effects, not the next business decision. It neither
chooses a candidate nor supplies a delivery action on behalf of the model.
"""
from copy import deepcopy
import hashlib

from tcm.dataops.data_access import canonical

HISTORY_PAIRS=3


def search_key(source_ids,query):
    return hashlib.sha256(canonical([sorted(source_ids),query.strip()]).encode()).hexdigest()


def affordances(context):
    root={x['source_id'] for x in context['candidates']}
    unread=[sid for sid in context['unread'] if sid in root]
    decided=set(context.get('decided_source_ids',[]))
    inspected={x['source_id']:x for x in context['inspections'] if x['source_id'] in root}
    searchable=[sid for sid,x in inspected.items() if len(x['candidates'])==1 and
                x['mapping_status'] not in ('unmatched','source_mismatch','ambiguous','candidate_selected_pending')]
    ambiguous=[sid for sid,x in inspected.items() if x['mapping_status']=='ambiguous' and sid not in decided]
    refs=[ref for ref in context['evidence_refs'] if ref not in set(context['evidence_already_read'])]
    delivery_ready=(context['listed'] and
        (not unread or len(context['eligible'])>=context['target_count'] and
         all(sid in inspected and sid not in unread for sid in context['parent_records'])))
    if context.get('task_mode')=='audit_named':
        delivery_ready=context['listed'] and not unread and root<=set(inspected)
    required=(sorted(root) if context.get('task_mode')=='audit_named' else list(context['parent_records']))
    deliverable=required if required else [sid for sid in context['eligible'] if sid in inspected]
    count=len(required) if required else min(context['target_count'],len(deliverable))
    tools=[]
    if not context['listed']:tools.append('list_candidates')
    else:
        if unread:tools.append('inspect_candidates')
        if searchable:tools.append('search_documents')
        if refs:tools.append('read_evidence')
        if ambiguous:tools.append('ask_user')
        if delivery_ready:tools.append('deliver_result')
    return {'tools':tools,'inspectable_source_ids':unread,'searchable_source_ids':searchable,
            'ambiguous_source_ids':ambiguous,'unread_evidence_ids':refs,
            'deliverable_source_ids':deliverable,'delivery_ready':bool(delivery_ready),
            'delivery_min_items':count,'delivery_max_items':count,'required_delivery_source_ids':required,
            'delivery_requires_partial':context.get('task_mode')!='audit_named' and len(context['eligible'])<context['target_count'],
            'partial_requires_explicit_flag':True,'selection_is_model_owned':True}


def action_rejection(context,tool,args):
    """Host-side defense, including for scripted or once-planned actions."""
    allowed=affordances(context)
    root={x['source_id'] for x in context['candidates']}
    if tool=='list_candidates':return 'already_listed' if context['listed'] else None
    if not context['listed']:return 'list_candidates_required'
    if 'source_ids' in args and set(args['source_ids'])-root:return 'outside_candidate_scope'
    if tool=='inspect_candidates' and set(args['source_ids'])-set(allowed['inspectable_source_ids']):
        return 'no_unread_candidate_fields'
    if tool=='search_documents':
        if set(args['source_ids'])-set(allowed['searchable_source_ids']):return 'inspect_unique_candidates_first'
        if search_key(args['source_ids'],args['query']) in context.get('completed_searches',[]):return 'search_already_run'
    if tool=='read_evidence':
        if set(args['evidence_ids'])-set(context['evidence_refs']):return 'unknown_evidence_handle'
        if set(args['evidence_ids'])-set(allowed['unread_evidence_ids']):return 'evidence_already_read'
    if tool=='ask_user' and args['source_id'] not in allowed['ambiguous_source_ids']:return 'no_source_ambiguity'
    if tool=='deliver_result':
        if not allowed['delivery_ready']:return 'target_or_fields_incomplete'
        ids=args['source_ids']
        if len(ids)>context['target_count']:return 'too_many_delivery_records'
        required=allowed['required_delivery_source_ids']
        if required and set(ids)!=set(required):
            return 'preserve_named_records' if context.get('task_mode')=='audit_named' else 'preserve_parent_records'
        if set(ids)-set(allowed['deliverable_source_ids']):return 'unqualified_delivery_records'
        if len(ids)!=allowed['delivery_min_items']:return 'delivery_count_mismatch'
        if allowed['delivery_requires_partial'] and not args['allow_partial']:return 'target_or_fields_incomplete'
    return None


def history_item(step,tool,args,observation):
    # Keep handles, states and short purposes, not old source bodies. Full latest
    # read text appears only in the current bounded observation.
    result={k:deepcopy(v) for k,v in observation.items() if k in (
        'tool','status','reason','returned','unknown_names','reused_cells','inspected',
        'search_status','evidence_ids','delivered','qualified','host_verified','made_progress','consecutive_no_progress')}
    if 'fragments' in observation:
        result['evidence_ids']=[f['evidence_id'] for f in observation['fragments']]
        result['text_omitted_from_history']=True
    result['summary_only']=True
    return {'step':step,'tool':tool,'arguments':deepcopy(args),'result':result}


def bounded_context(context,limit):
    context=deepcopy(context)
    history=context.get('recent_actions',[])
    context['history_omitted_count']=max(0,context['completed_tool_actions']-len(history))
    while history and len(canonical(context).encode())>limit:
        history.pop(0);context['history_omitted_count']+=1
    return context


def messages(context,system):
    output=[{'role':'system','content':system}]
    for action in context.get('recent_actions',[]):
        identifier='r4_step_'+str(action['step'])
        output.append({'role':'assistant','content':'','tool_calls':[{
            'id':identifier,'type':'function','function':{
                'name':action['tool'],'arguments':canonical(action['arguments'])}}]})
        output.append({'role':'tool','tool_call_id':identifier,'name':action['tool'],
                       'content':canonical(action['result'])})
    snapshot={k:v for k,v in context.items() if k!='recent_actions'}
    output.append({'role':'user','content':canonical(snapshot)})
    return output
