"""Opt-in local workspace API, sharing the existing session owner boundary."""
import hashlib
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from features import env_bool
from tcm.dataops.contracts import CreateWorkspace, Query, CreateAgent, Answer, Recover, TaskQuestion, SubmitQuestion
from tcm.dataops.data_access import Sources, SourceChanged
from tcm.dataops.service import DataOpsService, ProposalNeedsInput
from tcm.dataops.workspace import Workspaces, Conflict
from tcm.dataops.baseline import execute


def enabled():
    return env_bool('ENABLE_DATAOPS')


def configured_sources():
    return Sources(os.environ['TCM_DB'], os.environ['ETCM_ENRICHMENT_DB'],
                   os.environ['PHARMACOPOEIA_INDEX'], os.getenv('TCM_DATASET_VERSION'))


def validate_origin(request):
    origin = request.headers.get('origin')
    if request.method != 'GET' and (request.headers.get('sec-fetch-site') == 'cross-site' or
            origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)):
        raise HTTPException(403, '请从本站页面操作。')


def create_router(memory_provider, cookie_name, *, service=None):
    from tcm.research_jobs import JobConflict, JobBusy, TERMINAL
    service = service or DataOpsService(memory_provider)
    queue, worker = service.queue, service.worker
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await worker.stop()
    router = APIRouter(lifespan=lifespan)

    def context(request, response):
        response.headers['Cache-Control'] = 'no-store'
        if not enabled():
            raise HTTPException(404, '资料工作台未启用。')
        validate_origin(request)
        memory = memory_provider()
        cookie = request.cookies.get(cookie_name())
        if not memory.valid_cookie(cookie):
            raise HTTPException(401, '请打开资料工作台初始化会话。')
        return (Workspaces(os.getenv('DATAOPS_ROOT') or Path(memory.path).with_name('dataops')),
                hashlib.sha256(cookie.encode()).hexdigest())

    def call(action):
        try:
            return action()
        except ProposalNeedsInput as exc:
            raise HTTPException(409, detail=exc.proposal) from None
        except KeyError:
            raise HTTPException(404, '记录或产物不存在。') from None
        except JobBusy:
            raise HTTPException(429, '已有任务运行或队列已满。') from None
        except (Conflict, SourceChanged, JobConflict):
            raise HTTPException(409, '请求或来源版本冲突；请刷新或使用新的请求编号。') from None
        except (OSError, sqlite3.Error, ImportError):
            raise HTTPException(503, '本地目录、表格依赖或存储暂不可用。') from None
        except ValueError:
            raise HTTPException(409, '资料契约校验失败，工作区已保留失败状态。') from None

    @router.get('/dataops', response_class=HTMLResponse)
    def page(request: Request):
        if not enabled():
            raise HTTPException(404, '资料工作台未启用。')
        response = HTMLResponse(Path(__file__).with_name('workbench.html').read_text(),
                                headers={'Cache-Control': 'no-store'})
        memory = memory_provider()
        if not memory.valid_cookie(request.cookies.get(cookie_name())):
            response.set_cookie(cookie_name(), memory.new_cookie(), httponly=True, samesite='lax',
                                secure=env_bool('CHAT_COOKIE_SECURE'), max_age=memory.ttl_seconds)
        return response

    def agent_enabled():
        if not env_bool('ENABLE_DATAOPS_AGENT') or os.getenv('DATAOPS_DRIVER') != 'synthetic':
            raise HTTPException(409, '动态模式尚未启用；当前仅开放明确标注的本地合成模型验证。')

    @router.get('/api/dataops/capabilities')
    def capabilities(request: Request, response: Response):
        context(request, response)
        return {'agent': env_bool('ENABLE_DATAOPS_AGENT') and os.getenv('DATAOPS_DRIVER') == 'synthetic',
                'driver': os.getenv('DATAOPS_DRIVER', 'disabled'), 'external_model_enabled': False,
                'question_compiler': 'local_bounded_grammar_v1'}

    @router.post('/api/dataops/proposals')
    def propose_question(body: TaskQuestion, request: Request, response: Response):
        store, owner = context(request, response)
        def action():
            with configured_sources() as sources:
                return service.proposal(store, owner, body, sources)
        return call(action)

    @router.post('/api/dataops/task-requests', status_code=202)
    async def submit_question(body: SubmitQuestion, request: Request, response: Response):
        store, owner = context(request, response); agent_enabled()
        try:
            return await service.submit(owner, body)
        except ProposalNeedsInput as exc:
            raise HTTPException(409, detail=exc.proposal) from None
        except Exception as exc:
            def failed():
                raise exc
            return call(failed)

    @router.post('/api/dataops/agent-runs', status_code=202)
    async def create_agent(body: CreateAgent, request: Request, response: Response):
        store, owner = context(request, response); agent_enabled()
        def action():
            # Fail before reserving if queue backend is incompatible.
            queue()
            with configured_sources() as sources:
                return service.enqueue(store, owner, body, sources)
        result = await asyncio.to_thread(call, action)
        worker.start()
        return result

    @router.get('/api/dataops/agent-runs/{identifier}')
    def get_agent(identifier: str, request: Request, response: Response):
        store, owner = context(request, response)
        def action():
            doc = store.get(owner, identifier)
            if not doc.get('job_id'):
                return {'workspace': doc, 'job': {'status': doc['status'], 'revision': 0}}
            job = queue().get(owner, doc['job_id'])
            # Read queue first, then its already-persisted workspace projection.
            # Otherwise a transition can pair a new waiting status with an old empty question.
            doc = store.get(owner, identifier)
            if job['status'] in ('cancelled', 'failed', 'interrupted') and doc['status'] not in ('complete', 'partial'):
                doc = store.project(owner, identifier, status=job['status'], error=job.get('reason'))
            return {'workspace': doc, 'job': job}
        return call(action)

    @router.post('/api/dataops/agent-runs/{identifier}/answer', status_code=202)
    async def answer_agent(identifier: str, body: Answer, request: Request, response: Response):
        store, owner = context(request, response); agent_enabled()
        def action():
            doc = store.get(owner, identifier)
            job = queue().answer_dataops(owner, doc['job_id'], body.model_dump(exclude={'job_revision'}),
                                        expected_revision=body.job_revision)
            worker.start()
            return job
        return call(action)

    @router.post('/api/dataops/agent-runs/{identifier}/recover', status_code=202)
    async def recover_agent(identifier: str, body: Recover, request: Request, response: Response):
        store, owner = context(request, response); agent_enabled()
        def action():
            doc = store.get(owner, identifier)
            job = queue().resume(owner, doc['job_id'], expected_revision=body.job_revision)
            worker.start()
            return job
        return call(action)

    @router.post('/api/dataops/agent-runs/{identifier}/cancel')
    def cancel_agent(identifier: str, request: Request, response: Response):
        store, owner = context(request, response)
        def action():
            doc = store.get(owner, identifier)
            result = queue().cancel(owner, doc['job_id'])
            if result['status'] == 'cancelled':
                store.project(owner, identifier, status='cancelled', pending=None)
            return result
        return call(action)

    @router.get('/api/dataops/agent-runs/{identifier}/events')
    async def events_agent(identifier: str, request: Request, response: Response):
        store, owner = context(request, response)
        doc = call(lambda: store.get(owner, identifier))
        try:
            cursor = int(request.headers.get('last-event-id', '-1'))
            if cursor < -1:
                raise ValueError()
        except ValueError:
            raise HTTPException(422, '无效事件游标。') from None
        call(lambda: queue().events(owner, doc['job_id'], cursor))
        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                job, items = queue().events(owner, doc['job_id'], cursor)
                for item in items:
                    cursor = item['revision']
                    yield 'id: ' + str(cursor) + '\nevent: progress\ndata: ' + json.dumps(item, ensure_ascii=False) + '\n\n'
                if job['status'] in TERMINAL or job['status'] == 'waiting_input':
                    return
                yield ': heartbeat\n\n'
                await asyncio.sleep(.3)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store'})

    @router.post('/api/dataops/query')
    def query(body: Query, request: Request, response: Response):
        context(request, response)
        def action():
            with configured_sources() as sources:
                data = sources.query(body)
                data['records'] = [{'source_id': r['source_record_id'],
                                    'name': r['fields']['name_pinyin']['value'],
                                    'medicinal_part': r['fields']['medicinal_part']['value']} for r in data['records']]
                return data
        return call(action)

    @router.post('/api/dataops/workspaces')
    def create(body: CreateWorkspace, request: Request, response: Response):
        store, owner = context(request, response)
        def action():
            with configured_sources() as sources:
                doc, created = execute(store, owner, body.request_key, sources, body.goal)
                response.status_code = 201 if created else 200
                return doc
        return call(action)

    @router.get('/api/dataops/workspaces')
    def listing(request: Request, response: Response):
        store, owner = context(request, response)
        return call(lambda: {'items': store.listing(owner)})

    @router.get('/api/dataops/workspaces/{identifier}')
    def get(identifier: str, request: Request, response: Response):
        store, owner = context(request, response)
        return call(lambda: store.get(owner, identifier))

    @router.get('/api/dataops/workspaces/{identifier}/artifact')
    def download(identifier: str, request: Request, response: Response):
        store, owner = context(request, response)
        path, _ = call(lambda: store.artifact(owner, identifier))
        return FileResponse(path, filename='dataops-' + identifier + '-' + path.name,
                            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            headers={'Cache-Control': 'no-store'})

    return router
