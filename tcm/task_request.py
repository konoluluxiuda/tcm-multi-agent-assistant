"""Local literal suggestions followed by explicit user inventory review. No model."""
from copy import deepcopy
import re

from tcm.research_task_fixtures import NAMES
from tcm.request_coverage import ENTITIES,scope_model
from tcm.task_plan import TaskPlan,LocalTaskPlan,clarification

DELIVERABLE_HINTS={'source_relations':('共同靶点','来源关系','共同标识'),
                   'evidence_review':('研究记录','论文样例'),
                   'medicinal_part_table':('药用部位',)}

LOCAL_DELIVERABLE_HINTS={**DELIVERABLE_HINTS,'evidence_review':('文献','论文','研究记录')}


def local_deliverable_hints(question):
    """An explicit identifier namespace plus intersection is a relation request."""
    if re.search(r'(?<![A-Za-z0-9])UniProt(?![A-Za-z0-9])',question,re.I) and '交集' in question:
        return {**LOCAL_DELIVERABLE_HINTS,
                'source_relations':(*LOCAL_DELIVERABLE_HINTS['source_relations'],'交集')}
    return LOCAL_DELIVERABLE_HINTS


def capture_chat_review(result):
    """Persist only the pending local question, never a tool result or source text."""
    if not isinstance(result,dict) or result.get('topic')!='research_task_review' or result.get('can_create_task') is not True:
        return None
    proposal=result.get('task_proposal') or {}
    question=proposal.get('question')
    if proposal.get('mode')=='etcm_question' and isinstance(question,str) and 1<=len(question)<=400:
        return {'kind':'local_task_review_v1','question':question}
    return None


def chat_review_from_context(context):
    if not isinstance(context,dict) or context.get('kind')!='agent_contexts_v1':return None
    pending=context.get('task_review')
    if (not isinstance(pending,dict) or set(pending)!={'kind','question'} or
            pending['kind']!='local_task_review_v1' or not isinstance(pending['question'],str) or
            not 1<=len(pending['question'])<=400):return None
    return {'topic':'research_task_review','question':pending['question']}


def draft_request(question,*,entities=ENTITIES,hints=DELIVERABLE_HINTS,local=False):
    if not isinstance(question,str) or not question.strip() or len(question)>400:
        raise ValueError('Question must contain 1–400 characters')
    anchors=[]
    for name in entities:
        if (name.lower() not in question.lower()) if local and name.isascii() else (name not in question):continue
        # ASCII catalog names require token boundaries; never match Alpha in Alphabet.
        pattern=(r'(?<![a-zA-Z0-9])'+re.escape(name)+r'(?![a-zA-Z0-9])') if local and name.isascii() else re.escape(name)
        match=re.search(pattern,question,flags=re.I if local else 0)
        if match:anchors.append({'kind':'entity','item':name,'text':match.group(),'start':match.start(),'end':match.end()})
    anchors.sort(key=lambda a:(a['start'],-len(a['text']),a['item']))
    entities=[a['item'] for a in anchors]
    deliverables=[]
    for item,tokens in hints.items():
        found=[token for token in tokens if token in question]
        if found:
            token=min(found,key=question.index);start=question.index(token)
            deliverables.append(item);anchors.append({'kind':'deliverable','item':item,'text':token,'start':start,'end':start+len(token)})
    return {'version':1,'extractor':'local_literal_suggestions','entities':entities,
            'deliverables':deliverables,'other_requirements':[],'anchors':anchors,
            'semantic_completeness_verified':False}


def require_request_review(task):
    if 'request_draft' not in task:return
    review=task.get('request_review') or {}
    if review.get('confirmed_complete') is not True or review.get('confirmed_scope')!=task.get('request_scope'):
        raise ValueError('Review the original request inventory before planning')


def confirm_request(task,scope,*,confirm_complete):
    if task['status']!='waiting_request' or (task.get('pending') or {}).get('kind')!='request':
        raise ValueError('Task is not waiting for request review')
    if confirm_complete is not True:raise ValueError('Explicit complete-inventory confirmation required')
    scope=scope_model(task).model_validate(scope)
    if scope.goal_to_confirm or (not scope.deliverables and not scope.other_requirements):
        raise ValueError('Confirm at least one requirement, including unsupported requirements')
    local=task.get('data_mode')=='etcm_local'
    names=scope.entities if local else [name for name in scope.entities if name in NAMES]
    intent=('evidence_review' if not local and 'evidence_review' in scope.deliverables else
            'source_relations' if 'source_relations' in scope.deliverables else None)
    model=LocalTaskPlan if local else TaskPlan
    plan=model(intent=intent,herb_one=names[0] if names else None,herb_two=names[1] if len(names)>1 else None)
    original=task['request_draft']
    removed={'entities':[n for n in original['entities'] if n not in scope.entities],
             'deliverables':[d for d in original['deliverables'] if d not in scope.deliverables]}
    task.update(request_scope=scope.model_dump(),request_review={'confirmed_complete':True,
                'confirmed_scope':scope.model_dump(),'removed_suggestions':removed},
                plan=plan.model_dump(),proposed_plan=plan.model_dump(),names=plan.names(),
                plan_review_required=True,status='waiting_plan',pending=clarification(plan,review=True,entities=scope.entities))
    task['events'].append({'kind':'user_selection','phase':'request',
        'selection':{'request_scope':scope.model_dump(),'confirm_complete':True},
        'request_review':deepcopy(task['request_review'])})
    return task
