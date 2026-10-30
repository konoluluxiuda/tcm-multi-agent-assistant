"""Existing queue + A journal for validated, local-only chat plans."""
from contextlib import closing,nullcontext
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from features import env_bool
from tcm.research_jobs import storage,JobConflict,TERMINAL
from tcm.research_plan_intent import validate_question
from tcm.research_evidence_store import for_run,canonical
from tcm.research_run_durable import RunJournal


def enabled():return env_bool('ENABLE_RESEARCH_TASKS') and env_bool('ENABLE_RESEARCH_PLAN_JOBS')


def journal_path(path):return Path(path).with_name('research_plan_journal.sqlite3')


def code_revision():
    from tcm.runtime_versions import framework_versions
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}
    return hashlib.sha256(canonical({'code':files,'framework':framework_versions()}).encode()).hexdigest()


def make_payload(question,plan,selections,stamp,scope):
    return {'kind':'analysis_plan','question':question,'plan':plan.model_dump(),
            'selections':selections,'signature':stamp,'scope':scope.model_dump(),'code':code_revision()}


def validate_payload(payload,*,resolve=True):
    from tcm.research_plan_run import signature,compile_scope
    if not enabled():raise JobConflict('Background chat disabled')
    plan=validate_question(payload['plan'],payload['question'])
    if (payload['kind']!='analysis_plan' or code_revision()!=payload['code'] or
            signature(plan)!=payload['signature']):raise JobConflict('Code or sources changed')
    if any(s.call.tool in ('pharmacopoeia_lookup','pair_evidence_report') for s in plan.steps) and not env_bool('ENABLE_PHARMACOPOEIA_RESEARCH'):
        raise JobConflict('Pharmacopoeia disabled')
    from tcm.research_run_scope import RunScope
    scope=RunScope.model_validate(payload['scope'])
    if resolve:
        compiled,unresolved=compile_scope(plan,payload['selections'])
        if unresolved or canonical(compiled.model_dump())!=canonical(scope.model_dump()):raise JobConflict('Selected sources changed')
    return plan,scope


def submit(owner,path,payload,key=None):
    from tcm.research_plan_run import KIND
    validate_payload(payload)
    doc,created=storage(path).enqueue(owner,payload['question'],key or uuid4().hex,payload=payload)
    focus={'kind':KIND,'plan':payload['plan'],'signature':payload['signature'],'run_id':doc['id'],'job':True}
    return view(doc),focus,created


def view(doc):
    from tcm.research_plan_run import response
    result=doc.get('result')
    if doc['status'] in TERMINAL and result and result.get('display'):return result['display']
    text={'queued':'研究任务已排队。','running':'研究任务正在后台执行。','cancelled':'研究任务已取消。',
          'interrupted':'研究任务已中断，执行记录已保留。','failed':'研究任务未能完成，请核对来源或执行版本。'}
    return response(doc['status'],text.get(doc['status'],'研究任务尚未完成。'),
        run_id=doc['id'],job_id=doc['id'],job_revision=doc['revision'],recovery=doc.get('recovery'),
        stop_reason=doc.get('reason',''),execution={'mode':'local_declared_actions',
            'tool_calls':doc.get('snapshot',{}).get('tool_calls',0),
            'action_calls':doc.get('snapshot',{}).get('action_calls',0)})


async def execute_job(owner,doc,*,path,on_state,guard):
    from tcm.research_plan_run import signature,finalize
    from tcm.research_run_local import run_local_actions
    payload=doc['payload'];plan,scope=validate_payload(payload)
    evidence=for_run(path,owner,doc['id']);location=journal_path(path)
    journal=RunJournal(location,owner,doc['id'],{'kind':'local-chat-plan-v1','payload':payload},guard=guard)
    with journal.locked():
        with closing(sqlite3.connect(location)) as db:
            old=db.execute('SELECT owner FROM runs WHERE id=?',(doc['id'],)).fetchone()
        if old and old[0]!=owner:raise KeyError(doc['id'])
        journal.resume=bool(old)
        result=await run_local_actions(os.getenv('ETCM_DISEASE_DB'),scope,herbs=os.getenv('TCM_DB'),
            enrichment=os.getenv('ETCM_ENRICHMENT_DB'),pharmacopoeia=os.getenv('PHARMACOPOEIA_INDEX'),
            evidence_store=evidence,on_state=on_state,journal=journal)
    if signature(plan)!=payload['signature']:raise JobConflict('Source changed during execution')
    display,focus=finalize(result,plan,payload['question'],payload['selections'],payload['signature'],doc['id'],evidence)
    return {**result,'display':display,'next_focus':focus}


def get(owner,identifier,path,*,verify=True):
    doc=storage(path).get(owner,identifier)
    if doc.get('payload',{}).get('kind')!='analysis_plan':raise KeyError(identifier)
    if verify:validate_payload(doc['payload'],resolve=False)
    result=doc.get('result') or {}
    evidence=for_run(path,owner,identifier)
    with evidence.store.activity():evidence.view(result.get('run',{}).get('evidence',{}))
    return doc


def cleanup(owner,identifier,path,queue):
    """Do not delete evidence or journals while an old worker still owns a lock."""
    doc=queue.get(owner,identifier)
    if doc['status'] not in TERMINAL:raise JobConflict('Active task')
    evidence=for_run(path,owner,identifier);location=journal_path(path)
    journal=RunJournal(location,owner,identifier,'cleanup')
    with journal.locked() if location.exists() else nullcontext():
        with evidence.store.activity(exclusive=True):
            # The queue's transactional terminal check remains the authority.
            queue.delete(owner,identifier)
            with sqlite3.connect(evidence.store.path) as db:
                db.execute('DELETE FROM evidence WHERE owner=? AND run_id=?',(owner,identifier))
            if location.exists():
                with sqlite3.connect(location) as db:db.execute('DELETE FROM runs WHERE owner=? AND id=?',(owner,identifier))
