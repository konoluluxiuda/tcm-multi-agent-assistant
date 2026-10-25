"""Owner-scoped durable jobs. Closing an SSE subscription never cancels a job."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter,HTTPException,Request,Response,Query
from fastapi.responses import StreamingResponse
from pydantic import Field, BaseModel, field_validator
from tcm.research_run_api import ProfileRequest,enabled
from tcm.research_jobs import JobStore,ResearchWorker,JobConflict,JobBusy,TERMINAL


class SubmitJob(ProfileRequest):
    request_key: str = Field(min_length=8,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')


class SubmitReport(BaseModel):
    model_config = {'extra': 'forbid'}
    herb: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=400)
    request_key: str = Field(min_length=8,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')

    @field_validator('herb', 'question')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Query must not be blank')
        return value.strip()


class SubmitPairReport(BaseModel):
    model_config = {'extra': 'forbid'}
    herbs: list[str] = Field(min_length=2, max_length=2)
    question: str = Field(min_length=1, max_length=400)
    request_key: str = Field(min_length=8,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')

    @field_validator('herbs')
    @classmethod
    def valid_pair(cls, value):
        from tcm.pair_research_report import validate_pair
        return validate_pair(value)

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Question must not be blank')
        return value.strip()


def create_router(memory_provider,cookie_name,*,runner=None):
    def storage():
        configured=os.getenv('RESEARCH_JOB_DB')
        path=Path(configured) if configured else memory_provider().path.with_name('research_jobs.sqlite3')
        return JobStore(path)
    worker=ResearchWorker(storage,lambda:os.environ['ETCM_DISEASE_DB'],**({'runner':runner} if runner else {}))
    @asynccontextmanager
    async def lifespan(app):
        if enabled():worker.start()
        try:yield
        finally:await worker.stop()
    router=APIRouter(lifespan=lifespan)
    router.research_worker=worker

    def authorize(request):
        if not enabled():raise HTTPException(404,'后台研究未启用。')
        origin=request.headers.get('origin')
        if request.method not in ('GET','HEAD') and (request.headers.get('sec-fetch-site')=='cross-site' or
            origin and (urlsplit(origin).scheme,urlsplit(origin).netloc)!=(request.url.scheme,request.url.netloc)):
            raise HTTPException(403,'请从本站页面操作。')
        cookie=request.cookies.get(cookie_name())
        if not memory_provider().valid_cookie(cookie):raise HTTPException(401,'请刷新页面初始化会话。')
        return hashlib.sha256(cookie.encode()).hexdigest()

    def translate(exc):
        if isinstance(exc,KeyError):return HTTPException(404,'未找到本会话的任务。')
        if isinstance(exc,JobBusy):return HTTPException(429,'已有任务运行或队列已满，请稍后再试。')
        if isinstance(exc,JobConflict):return HTTPException(409,'任务状态、请求标识或事件位置冲突，请刷新查看。')
        return HTTPException(503,'任务存储暂不可用，未启动无记录执行。')

    @router.post('/api/research/jobs',status_code=202)
    async def submit(body:SubmitJob,request:Request,response:Response):
        owner=authorize(request)
        try:doc,created=storage().enqueue(owner,body.disease,body.request_key)
        except Exception as exc:raise translate(exc)
        worker.start()
        response.headers['Cache-Control']='no-store'
        return {'run_id':doc['id'],'status':doc['status'],'created':created}

    @router.post('/api/research/jobs/report',status_code=202)
    async def submit_report(body:SubmitReport,request:Request,response:Response):
        owner=authorize(request)
        payload={'kind':'cross_source','herb':body.herb,'question':body.question}
        try:doc,created=storage().enqueue(owner,body.herb+'：'+body.question,body.request_key,payload=payload)
        except Exception as exc:raise translate(exc)
        worker.start();response.headers['Cache-Control']='no-store'
        return {'run_id':doc['id'],'status':doc['status'],'created':created}

    @router.post('/api/research/jobs/compare',status_code=202)
    async def submit_pair(body:SubmitPairReport,request:Request,response:Response):
        owner=authorize(request)
        payload={'kind':'pair_report','herbs':body.herbs,'question':body.question}
        try:doc,created=storage().enqueue(owner,' / '.join(body.herbs)+'：'+body.question,body.request_key,payload=payload)
        except Exception as exc:raise translate(exc)
        worker.start();response.headers['Cache-Control']='no-store'
        return {'run_id':doc['id'],'status':doc['status'],'created':created}

    @router.get('/api/research/jobs')
    async def history(request:Request,response:Response):
        owner=authorize(request);response.headers['Cache-Control']='no-store'
        try:return {'items':[{k:d[k] for k in ('id','revision','question','status','updated')} for d in storage().list(owner)]}
        except Exception as exc:raise translate(exc)

    @router.get('/api/research/jobs/{identifier}')
    async def get(identifier:str,request:Request,response:Response):
        owner=authorize(request);response.headers['Cache-Control']='no-store'
        try:return storage().get(owner,identifier)
        except Exception as exc:raise translate(exc)

    @router.post('/api/research/jobs/{identifier}/cancel')
    async def cancel(identifier:str,request:Request):
        owner=authorize(request)
        try:return storage().cancel(owner,identifier)
        except Exception as exc:raise translate(exc)

    @router.delete('/api/research/jobs/{identifier}')
    async def delete(identifier:str,request:Request):
        owner=authorize(request)
        try:storage().delete(owner,identifier);return {'status':'deleted'}
        except Exception as exc:raise translate(exc)

    @router.get('/api/research/jobs/{identifier}/events')
    async def events(identifier:str,request:Request,after:int=Query(default=-1,ge=-1)):
        owner=authorize(request)
        cursor=request.headers.get('last-event-id')
        if cursor is not None:
            try:after=int(cursor)
            except ValueError:raise HTTPException(422,'无效事件序号。')
            if after< -1:raise HTTPException(422,'无效事件序号。')
        try:store=storage();store.events(owner,identifier,after)
        except Exception as exc:raise translate(exc)
        async def stream():
            cursor=after;idle=0
            while not await request.is_disconnected():
                try:doc,rows=store.events(owner,identifier,cursor)
                except Exception:
                    yield 'event: unavailable\ndata: {"message":"研究记录暂不可用，请刷新历史"}\n\n';return
                for row in rows:
                    cursor=row['revision'];idle=0
                    yield f'id: {cursor}\nevent: progress\ndata: {json.dumps(row,ensure_ascii=False)}\n\n'
                if doc['status'] in TERMINAL:
                    # A reconnect at the final cursor still receives completion.
                    yield 'event: done\ndata: '+json.dumps({'run_id':identifier,'status':doc['status']})+'\n\n';return
                idle+=1
                if idle%40==0:yield ': keep-alive\n\n'
                await asyncio.sleep(.25)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})
    return router
