"""Local ETCM relationship task. No model, HTTP, or synthetic study adapter."""
from copy import deepcopy
from contextlib import closing
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from tcm.research_task_store import TaskConflict
from tcm.task_errors import LocalSnapshotChanged, LocalUnavailable, LocalBudget
from tcm.task_tools import LocalTaskTools
from tcm.task_plan import LocalTaskPlan,confirm_plan
from tcm.task_request import draft_request,confirm_request,require_request_review
from tcm.request_coverage import cover_report,require_covered_scope


def local_request_draft(question,version):
    """Only literal catalog labels; no identity inference or relation queries."""
    from tcm.chat import DEFAULT_DATABASE
    from tcm.etcm_enrichment import connect
    try:
        with closing(connect(os.environ['ETCM_ENRICHMENT_DB'])) as con:
            names={r[0] for r in con.execute('SELECT DISTINCT c.name_zh FROM name_candidates c '
                'JOIN herbs h ON h.source_record_id=c.herb_id WHERE h.dataset_version=?',(version,))}
        path=Path(os.getenv('TCM_DB') or DEFAULT_DATABASE).resolve()
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
            names.update(r[0] for r in con.execute('SELECT name_key FROM names WHERE dataset_version=?',(version,)))
            names.update(r[0] for r in con.execute('SELECT source_record_id FROM records WHERE dataset_version=?',(version,)))
    except (KeyError,OSError,ValueError,sqlite3.Error):
        raise LocalUnavailable('Local name catalog unavailable') from None
    return draft_request(question,entities=sorted(n for n in names if n and len(n)<=100),local=True,
        hints={'source_relations':('共同靶点','来源关系','共同标识'),
               'evidence_review':('文献','论文','研究记录'), 'medicinal_part_table':('药用部位',)})


def file_signature(path):
    stat=path.stat()
    return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns


@lru_cache(maxsize=16)
def file_sha256(path,signature):
    # Runtime catalogs are read-only. Rehash on replacement or any file/WAL write,
    # rather than rescanning a ~1 GiB relation catalog on every page request.
    if file_signature(path)!=signature:raise LocalSnapshotChanged()
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    if file_signature(path)!=signature:raise LocalSnapshotChanged()
    return h.hexdigest()


def snapshot():
    from tcm.chat import DEFAULT_DATABASE
    from tcm.relation_memory import metadata
    try:
        meta=metadata()
        if os.getenv('TCM_DATASET_VERSION') and os.environ['TCM_DATASET_VERSION']!=meta['dataset_version']:
            raise ValueError('Dataset version mismatch')
        fingerprints={}
        for label,path in [('herbs',Path(os.getenv('TCM_DB') or DEFAULT_DATABASE)),
                           ('relations',Path(os.environ['ETCM_ENRICHMENT_DB']))]:
            for suffix in ('','-wal'):
                source=Path(str(path)+suffix)
                if suffix and not source.exists():continue
                fingerprints[label+suffix]=file_sha256(source.resolve(),file_signature(source))
        return {'dataset_version':meta['dataset_version'],'source_fingerprint':meta['source_fingerprint'],
                'catalog_sha256':fingerprints}
    except (KeyError,OSError,ValueError,sqlite3.Error):
        raise LocalUnavailable('Local ETCM catalogs unavailable') from None


def relation_report(task, gaps):
    report={'data_mode':'etcm_local','complete':False,'local_steps_complete':bool(task['targets']),
        'source_ids':task['sources'],'selected_targets':task['targets'],
        'snapshot':task['snapshot'],'source_records':task.get('selected_source_records',[]),
        'relation_evidence':[r for r in task['relations'] if r['id'] in task['targets']],
        'study_evidence':[], 'common_target_total':task.get('common_target_total'), 'gaps':gaps,
        'notice':'真实ETCM本地来源关系核查，未调用模型或联网。相同UniProt仅为来源声明，未外部核验；数据库关联不是结合、机制、疗效或文献证据。抓取完整性未保证。'}
    if task.get('result_version',1)==1:
        return report  # Preserve frozen v1 semantics, including in-progress old tasks.
    from tcm.task_contracts import local_relation_completion
    completion=local_relation_completion(task)
    limitations=[report['notice'],
        '未检索或核验文献，文献核验不属于本地来源关系任务的完成条件。',
        '药材身份、UniProt映射及来源抓取完整性尚未外部核验；来源相同标识不证明机制或疗效。']
    if completion.outcome=='empty':limitations.append('已选来源未记录共同明确标识，不表示真实世界不存在共同作用。')
    report.update(result_version=2,completion=completion.model_dump(),
        complete=completion.status=='complete',local_steps_complete=completion.status=='complete',
        gaps=[] if completion.status=='complete' else gaps or ['本次请求的来源、查询或双方见证核对尚未完成。'],
        limitations=limitations)
    return cover_report(task,report)



class LocalRelationTaskEngine:
    def __init__(self,store):self.store=store

    def create(self,*,owner,names=None,question=None):
        if question is not None:
            if names is not None:raise ValueError('Question review cannot be combined with preselected names')
            pinned=snapshot()
            draft=local_request_draft(question,pinned['dataset_version'])
            if snapshot()!=pinned:raise LocalSnapshotChanged()
            return self.store.create({'result_version':2,'task_scope':'local_relation','owner':owner,
                'data_mode':'etcm_local','profile':'local_common_targets','snapshot':pinned,'question':question,
                'request_draft':draft,'plan':LocalTaskPlan().model_dump(),
                'names':[],'status':'waiting_request','pending':{'kind':'request'},
                'sources':[],'targets':[],'relations':[],'source_options':[],'events':[],
                'tool_calls':0,'max_tool_calls':12,'active_ms':0,'inflight':None,'report':None})
        if (not isinstance(names,list) or len(names)!=2 or any(not isinstance(n,str) or not n.strip() or len(n)>100 for n in names)
                or len({n.strip() for n in names})!=2):raise ValueError('Two distinct explicit names required')
        names=[n.strip() for n in names]
        return self.store.create({'result_version':2,'task_scope':'local_relation','owner':owner,'data_mode':'etcm_local','profile':'local_common_targets',
            'snapshot':snapshot(),'question':'核对 '+names[0]+' 与 '+names[1]+' 的来源声明共同靶点及选定靶点的双方原始记录。',
            'names':names,'status':'running','pending':None,'sources':[],'targets':[],'relations':[],
            'source_options':[],'events':[],'tool_calls':0,'max_tool_calls':12,'active_ms':0,'inflight':None,'report':None})

    def ensure(self,task):
        if snapshot()!=task['snapshot']:raise LocalSnapshotChanged('ETCM snapshot changed; create a new task')

    def load(self,identifier,revision=None):
        task=self.store.get(identifier)
        if task.get('data_mode')!='etcm_local':raise ValueError('Not a local ETCM task')
        if revision is not None and revision!=task['revision']:raise TaskConflict()
        return task

    def action(self,task,name,args,observation,result=None):
        if task['tool_calls']>=task['max_tool_calls']:raise LocalBudget()
        task['tool_calls']+=1
        task['events'].append({'kind':'tool','name':name,'args':args,'observation':deepcopy(observation)})
        if result is not None:
            task['events'][-1]['result']=result.model_dump(exclude={'data'})

    def invoke(self,task,name,args):
        require_request_review(task)
        require_covered_scope(task)
        if task['tool_calls']>=task['max_tool_calls']:raise LocalBudget()
        result=LocalTaskTools(task,self.ensure).invoke(name,args)
        if result.status=='error':
            if result.error.code=='snapshot_changed':raise LocalSnapshotChanged()
            if result.error.code in ('unknown_tool','invalid_arguments','invalid_state'):raise ValueError('Invalid local tool request')
            raise LocalUnavailable()
        return result

    def review_request(self,identifier,scope,*,revision,confirm_complete):
        task=self.load(identifier,revision)
        self.ensure(task)
        if 'request_draft' not in task:raise ValueError('Not a local question task')
        confirm_request(task,scope,confirm_complete=confirm_complete)
        return self.store.save(task)

    def run(self,identifier):
        task=self.load(identifier)
        if task['status']!='running':return task
        result=self.invoke(task,'resolve_sources',{'names':task['names']})
        data=result.data.model_dump();groups=data['groups']
        gaps=[g['name']+'：本地未返回来源候选，请核对已收录名称或来源ID后重新创建任务。'
              for g in groups if not g['options']]
        task['source_options']=data['records']
        self.action(task,'resolve_sources',{'names':task['names']},{'groups':groups},result)
        if gaps:task.update(status='partial',report=relation_report(task,gaps))
        else:task.update(status='waiting_sources',pending={'kind':'sources','options':groups})
        return self.store.save(task)

    def common(self,task,page):
        result=self.invoke(task,'common_targets',{'page':page})
        data=result.data.model_dump();rows=data['items']
        task['relations']=rows;task['common_target_total']=data['total']
        self.action(task,'common_targets',{'page':page},
                    {'total':data['total'],'page':page,'returned_count':len(rows),'items':rows},result)
        if not rows:
            task.update(pending=None,report=relation_report(task,[
                '已选两份来源未记录相同的明确UniProt标识；不表示真实世界不存在共同作用。',
                '未检索或核验文献；数据库来源关联不构成药材本体研究证据。']))
            task['status']='complete' if task['report']['complete'] else 'partial'
        else:
            task.update(status='waiting_targets',pending={'kind':'targets','options':[{'id':r['id']} for r in rows],
                'page':page,'total':data['total'],'returned_count':len(rows),'has_next':data['has_next'],
                'note':'仅按来源声明的明确UniProt标识求交集；每页最多20项，选择本页1至2项核对双方原始行。'})

    def choose(self,identifier,selection,*,revision,accept_reduced_scope=False):
        task=self.load(identifier,revision);pending=task.get('pending')
        if not pending:raise ValueError('No pending selection')
        self.ensure(task)
        require_request_review(task)
        if pending['kind']=='plan':
            confirm_plan(task,selection,accept_reduced_scope=accept_reduced_scope)
            task['events'].append({'kind':'user_selection','phase':'plan','selection':deepcopy(selection),
                'scope_acceptance':deepcopy(task['scope_acceptance'])})
            return self.store.save(task)
        if accept_reduced_scope:raise ValueError('Scope acceptance belongs to plan confirmation')
        require_covered_scope(task)
        if pending['kind']=='sources':
            if not isinstance(selection,dict) or set(selection)!=set(task['names']):raise ValueError('Choose both names')
            for group in pending['options']:
                if selection[group['name']] not in {o['id'] for o in group['options']}:raise ValueError('Unlisted source')
            ids=[selection[n] for n in task['names']]
            if len(set(ids))!=2:raise ValueError('Choose distinct sources')
            task['sources']=ids
            task['selected_source_records']=[next(r for r in task['source_options'] if r['source_record_id']==sid) for sid in ids]
            task['events'].append({'kind':'user_selection','selection':deepcopy(selection)})
            self.common(task,1)
        else:
            if (not isinstance(selection,list) or not 1<=len(selection)<=2 or any(not isinstance(s,str) for s in selection)
                    or len(set(selection))!=len(selection) or not set(selection)<={o['id'] for o in pending['options']}):
                raise ValueError('Choose one or two targets on the current page')
            task['targets']=selection[:]
            task['events'].append({'kind':'user_selection','selection':selection[:]})
            task['report']=relation_report(task,['未检索或核验文献；数据库来源关联不构成药材本体研究证据。',
                '来源药材身份、UniProt映射与关系抓取完整性尚未外部核验，不能据此推断机制或疗效。'])
            self.action(task,'finish_task',{},task['report'])
            task.update(status='complete' if task['report']['complete'] else 'partial',pending=None)
        return self.store.save(task)

    def page(self,identifier,page,*,revision):
        task=self.load(identifier,revision)
        if task['status']!='waiting_targets' or type(page) is not int or not 1<=page<=1000:raise ValueError('No target page')
        if task['tool_calls']>=task['max_tool_calls']-1:raise LocalBudget()
        self.common(task,page)
        return self.store.save(task)

    def references(self,identifier,*,revision):
        task=self.load(identifier,revision)
        if not task.get('report',{} ) or not task['report'].get('local_steps_complete'):
            raise ValueError('Finish source and target selection first')
        if task.get('reference_leads') is not None:return task
        if task['tool_calls']>=task['max_tool_calls']:raise LocalBudget()
        result=self.invoke(task,'source_references',{})
        leads=result.data.model_dump()
        task['reference_leads']=leads
        task['report']['reference_leads']=leads
        self.action(task,'source_references',{},leads,result)
        return self.store.save(task)
