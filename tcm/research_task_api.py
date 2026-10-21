"""Browser-owned synthetic task demo. No model factory or real corpus adapter."""
import hashlib
import os
from pathlib import Path
import sqlite3
from typing import Annotated,Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from agents.runtime import session_turn
from features import env_bool
from tcm.research_task import ResearchTaskEngine, ScriptedToolModel
from tcm.research_task_fixtures import SyntheticResearchTools
from tcm.research_task_local import LocalRelationTaskEngine, LocalSnapshotChanged, LocalUnavailable, LocalBudget
from tcm.research_task_store import ResearchTaskStore, TaskConflict, TaskLimit
from tcm.task_plan import TaskPlan
from tcm.request_coverage import RequestScope,LocalRequestScope,assess_coverage,ScopeDecisionRequired,ScopeMismatch


def enabled():
    return env_bool('ENABLE_RESEARCH_TASKS')


class CreateTask(BaseModel):
    model_config = {'extra': 'forbid'}
    mode: Literal['synthetic', 'synthetic_plan', 'synthetic_question', 'etcm_local', 'etcm_question'] = 'synthetic'
    question: str | None = Field(default=None,min_length=1,max_length=400)
    plan: TaskPlan | None = None
    request_scope: RequestScope | None = None
    names: list[str] | None = Field(default=None, min_length=2, max_length=2)
    profile: Literal['retry', 'direct', 'ingredient_only', 'withdrawn', 'wrong_object', 'unavailable', 'no_common'] = 'retry'

    @model_validator(mode='after')
    def validate_mode(self):
        if self.mode in ('synthetic_question','etcm_question'):
            if self.question is None or self.names is not None or self.plan is not None or self.request_scope is not None:
                raise ValueError('Question mode starts with an unconfirmed inventory')
            if self.mode=='etcm_question' and self.profile!='retry':raise ValueError('Local tasks have no synthetic profile')
            return self
        if self.question is not None:raise ValueError('Questions require question mode')
        if self.mode=='synthetic_plan':
            if self.names is not None or self.plan is None:raise ValueError('Provide a synthetic plan, not real names')
            return self
        if self.plan is not None or self.request_scope is not None:raise ValueError('Plans and scope are only available in synthetic plan mode')
        if self.mode == 'etcm_local':
            if (self.names is None or any(not n.strip() or len(n)>100 for n in self.names)
                    or len({n.strip() for n in self.names})!=2 or self.profile!='retry'):
                raise ValueError('Provide two distinct names for local ETCM mode')
        elif self.names is not None:
            raise ValueError('Synthetic tasks do not accept real names')
        return self


class ChooseTask(BaseModel):
    model_config = {'extra': 'forbid'}
    revision: int = Field(strict=True, ge=0)
    selection: dict[str, str] | list[str]
    accept_reduced_scope: bool = Field(default=False,strict=True)


class ResumeTask(BaseModel):
    model_config = {'extra': 'forbid'}
    revision: int = Field(strict=True, ge=0)
    retry_interrupted: bool = Field(default=False, strict=True)


class TargetPage(BaseModel):
    model_config = {'extra': 'forbid'}
    revision: int = Field(strict=True, ge=0)
    page: int = Field(strict=True, ge=1, le=1000)


class ReviewRequest(BaseModel):
    model_config={'extra':'forbid'}
    revision:int=Field(strict=True,ge=0)
    request_scope:Annotated[RequestScope|LocalRequestScope,Field(discriminator='data_mode')]
    confirm_complete:bool=Field(default=False,strict=True)

    @field_validator('request_scope',mode='before')
    @classmethod
    def legacy_synthetic_default(cls,value):
        if isinstance(value,dict) and 'data_mode' not in value:
            return {'data_mode':'synthetic_only',**value}
        return value


def view(task):
    # No owner credentials, native messages or free model reasoning in the web API.
    keys = ('id', 'revision', 'result_version', 'task_scope', 'plan', 'proposed_plan', 'plan_review_required', 'plan_origin', 'request_draft', 'request_review', 'request_scope', 'scope_acceptance', 'names', 'question', 'profile', 'status', 'pending', 'sources', 'targets',
            'report', 'tool_calls', 'active_ms', 'max_tool_calls', 'failure')
    result = {k: task[k] for k in keys if k in task}
    result.update(data_mode=task['data_mode'], controller='local_readonly' if task['data_mode']=='etcm_local' else 'scripted', external_model_calls=(task.get('plan_origin') or {}).get('provider_calls',0),
                  recovery_required=bool(task.get('inflight')) or task['status']=='interrupted')
    result['actions'] = [
        {k: e[k] for k in ('kind', 'name', 'args', 'observation', 'result', 'duration_ms', 'selection', 'scope_acceptance', 'request_review', 'note') if k in e}
        for e in task['events']]
    if task.get('request_scope'):result['request_coverage']=assess_coverage(task)
    return result


def create_router(memory_provider, cookie_name):
    router = APIRouter()

    def storage(request: Request):
        if not enabled():
            raise HTTPException(404, '研究任务页面未启用。')
        if request.method not in ('GET', 'HEAD'):
            origin = request.headers.get('origin')
            if (request.headers.get('sec-fetch-site') == 'cross-site' or
                    (origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) !=
                     (request.url.scheme, request.url.netloc))):
                raise HTTPException(403, '请从本站任务页面提交操作。')
        try:
            memory = memory_provider()
            path = Path(os.getenv('RESEARCH_TASK_DB') or memory.path.with_name('research_tasks.sqlite3'))
            if path.resolve() == memory.path.resolve():
                raise ValueError('Task and conversation databases must differ')
            yield ResearchTaskStore(path)
        except LocalSnapshotChanged:
            raise HTTPException(409, 'ETCM数据快照已变化，请重新创建任务并确认来源；原任务记录已保留。')
        except LocalUnavailable:
            raise HTTPException(503, '本地ETCM目录暂不可用，未调用模型或联网补答。')
        except LocalBudget:
            raise HTTPException(409, '本任务查询额度已耗尽，请查看已保存记录或创建新任务。')
        except TaskConflict:
            raise HTTPException(409, '任务已被其他页面修改，请刷新后重试。')
        except TaskLimit:
            raise HTTPException(409, '每个浏览器最多保留20个演示任务，请先删除旧任务。')
        except ScopeDecisionRequired:
            raise HTTPException(422, '原请求仍有未覆盖项。请核对当前执行范围，明确同意仅执行该范围；其余要求继续保留为未完成。')
        except ScopeMismatch:
            raise HTTPException(422, '当前计划超出声明的请求范围，缩小范围确认不能用于增加对象或任务。')
        except (sqlite3.Error, OSError):
            raise HTTPException(503, '任务存储暂不可用，请稍后重试。')
        except ValueError:
            raise HTTPException(422, '所选选项、任务状态或资料版本无效，请刷新任务。')

    def owner(request, response, *, create=False):
        memory = memory_provider()
        name = cookie_name() + '_tasks'
        cookie = request.cookies.get(name)
        if not memory.valid_cookie(cookie):
            if not create:
                raise HTTPException(404, '未找到本浏览器的任务。')
            cookie = memory.new_cookie()
        response.set_cookie(name, cookie, httponly=True, samesite='lax',
                            secure=env_bool('CHAT_COOKIE_SECURE'), max_age=365*86400)
        response.headers['Cache-Control'] = 'private, no-store'
        return hashlib.sha256(cookie.encode()).hexdigest()

    def owned(store, identifier, credential):
        try:
            task = store.get(identifier)
        except KeyError:
            raise HTTPException(404, '未找到本浏览器的任务。')
        if task.get('owner') != credential:
            raise HTTPException(404, '未找到本浏览器的任务。')
        return task

    def engine(store, task):
        if task.get('data_mode') == 'etcm_local':
            return LocalRelationTaskEngine(store)
        return ResearchTaskEngine(store, ScriptedToolModel(), SyntheticResearchTools(task['profile']))

    @router.get('/research-tasks')
    def page(store=Depends(storage)):
        return FileResponse(Path(__file__).with_name('research_tasks.html'),
                            headers={'Cache-Control': 'no-store'})

    @router.get('/research-tasks.js')
    def javascript(store=Depends(storage)):
        return FileResponse(Path(__file__).with_name('research_tasks.js'), media_type='text/javascript',
                            headers={'Cache-Control': 'no-store'})

    @router.get('/api/research/tasks')
    def listing(request: Request, response: Response, store=Depends(storage)):
        credential = owner(request, response, create=True)
        return {'items': [view(t) for t in store.list_owned(credential)]}

    @router.post('/api/research/tasks')
    def create(body: CreateTask, request: Request, response: Response, store=Depends(storage)):
        credential = owner(request, response, create=True)
        task_engine = engine(store, {'profile': body.profile, 'data_mode':'etcm_local' if body.mode=='etcm_question' else body.mode})
        task = (task_engine.create(owner=credential,names=body.names) if body.mode=='etcm_local'
                else task_engine.create(owner=credential,question=body.question) if body.mode in ('synthetic_question','etcm_question')
                else task_engine.create(owner=credential,plan=body.plan.model_dump(),request_scope=body.request_scope.model_dump() if body.request_scope else None) if body.mode=='synthetic_plan'
                else task_engine.create(owner=credential))
        return view(task_engine.run(task['id']))

    @router.get('/api/research/tasks/{identifier}')
    def get(identifier: str, request: Request, response: Response, store=Depends(storage)):
        return view(owned(store, identifier, owner(request, response)))

    @router.post('/api/research/tasks/{identifier}/choose')
    def choose(identifier: str, body: ChooseTask, request: Request, response: Response, store=Depends(storage)):
        with session_turn('research-task:' + identifier):
            task = owned(store, identifier, owner(request, response))
            task_engine = engine(store, task)
            if body.accept_reduced_scope:
                task_engine.choose(identifier,body.selection,revision=body.revision,accept_reduced_scope=True)
            else:task_engine.choose(identifier, body.selection, revision=body.revision)
            return view(task_engine.run(identifier))

    @router.post('/api/research/tasks/{identifier}/request')
    def review_request(identifier:str,body:ReviewRequest,request:Request,response:Response,store=Depends(storage)):
        with session_turn('research-task:'+identifier):
            task=owned(store,identifier,owner(request,response))
            if task['data_mode']!=body.request_scope.data_mode:raise HTTPException(422,'请求清单与任务的数据模式不符。')
            return view(engine(store,task).review_request(identifier,body.request_scope.model_dump(),
                revision=body.revision,confirm_complete=body.confirm_complete))

    @router.post('/api/research/tasks/{identifier}/references')
    def references(identifier: str, body: ResumeTask, request: Request, response: Response, store=Depends(storage)):
        with session_turn('research-task:' + identifier):
            task = owned(store, identifier, owner(request, response))
            if task['data_mode']!='etcm_local' or body.retry_interrupted:
                raise HTTPException(422, '仅已完成本地关系核对的ETCM任务可读取来源文献ID。')
            return view(engine(store,task).references(identifier,revision=body.revision))

    @router.post('/api/research/tasks/{identifier}/targets-page')
    def targets_page(identifier: str, body: TargetPage, request: Request, response: Response, store=Depends(storage)):
        with session_turn('research-task:' + identifier):
            task = owned(store, identifier, owner(request, response))
            if task['data_mode']!='etcm_local':
                raise HTTPException(422, '分页仅用于本地ETCM任务。')
            return view(engine(store, task).page(identifier,body.page,revision=body.revision))

    @router.post('/api/research/tasks/{identifier}/resume')
    def resume(identifier: str, body: ResumeTask, request: Request, response: Response, store=Depends(storage)):
        with session_turn('research-task:' + identifier):
            task = owned(store, identifier, owner(request, response))
            if task['data_mode']=='etcm_local' and body.retry_interrupted:
                raise HTTPException(422, '本地ETCM任务没有模型重试步骤，请刷新并继续选择。')
            if task['revision'] != body.revision:
                raise TaskConflict()
            task_engine = engine(store, task)
            # Detect an unfinished reservation without replaying it on a GET.
            if task['status'] == 'running' and task.get('inflight'):
                task = task_engine.run(identifier)
            if body.retry_interrupted:
                task_engine.retry_interrupted(identifier, revision=task['revision'])
            elif task['status'] != 'running':
                raise ValueError('Explicit interruption recovery required')
            return view(task_engine.run(identifier))

    @router.delete('/api/research/tasks/{identifier}')
    def delete(identifier: str, request: Request, response: Response,
               revision: int = Query(..., ge=0), store=Depends(storage)):
        with session_turn('research-task:' + identifier):
            owned(store, identifier, owner(request, response))
            store.delete(identifier, revision)
            return {'status': 'deleted'}

    @router.get('/api/research/tasks/{identifier}/report')
    def export(identifier: str, request: Request, response: Response, store=Depends(storage)):
        task = owned(store, identifier, owner(request, response))
        if task['report'] is None:
            raise HTTPException(409, '任务尚未生成报告。')
        # Reuse a pure formatter; no CLI controller/model imports at module scope.
        from tcm.research_task_report import markdown
        result = Response(markdown(task), media_type='text/markdown', headers={
            'Cache-Control': 'private, no-store',
            'Content-Disposition': 'attachment; filename="'+('etcm-local-task.md' if task['data_mode']=='etcm_local' else 'synthetic-research-task.md')+'"'})
        result.headers['set-cookie'] = response.headers['set-cookie']
        return result

    return router
