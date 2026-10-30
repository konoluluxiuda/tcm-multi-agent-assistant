"""Explicitly enabled, authenticated synthetic loop lifecycle; never a provider route."""
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
from fastapi import APIRouter,HTTPException,Request,Response
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from features import env_bool
from tcm.research_run import Limits
from tcm.research_run_demo import OfflineModel,synthetic_tool
from tcm.research_run_durable import run_durable,RunConflict
from tcm.research_loop_store import get,records,view,delete


class Create(BaseModel):
    model_config={'extra':'forbid'}
    pause_after_observation: bool = False


class Revision(BaseModel):
    model_config={'extra':'forbid'}
    revision: int = Field(ge=1,strict=True)


class Paused(Exception):pass


def enabled():return env_bool('ENABLE_SYNTHETIC_LOOP')


def create_router(memory_provider,cookie_name):
    router=APIRouter();active=set()
    def storage():return Path(os.getenv('RESEARCH_LOOP_DB') or memory_provider().path.with_name('research_loop.sqlite3'))
    def authorize(request):
        if not enabled():raise HTTPException(404,'合成循环演示未启用。')
        origin=request.headers.get('origin')
        if request.method!='GET' and (request.headers.get('sec-fetch-site')=='cross-site' or origin and
                (urlsplit(origin).scheme,urlsplit(origin).netloc)!=(request.url.scheme,request.url.netloc)):
            raise HTTPException(403,'请从本站操作。')
        cookie=request.cookies.get(cookie_name())
        if not memory_provider().valid_cookie(cookie):raise HTTPException(401,'请刷新页面初始化会话。')
        return hashlib.sha256(cookie.encode()).hexdigest()
    def error(exc):
        if isinstance(exc,KeyError):return HTTPException(404,'未找到本会话的循环记录。')
        if isinstance(exc,RunConflict):return HTTPException(409,'记录已变化、正在执行或执行版本不兼容，请刷新。')
        return HTTPException(503,'循环记录暂不可用；未自动重试。')
    @router.get('/research-loop.js')
    def javascript():return FileResponse(Path(__file__).with_name('research_loop.js'),media_type='application/javascript')
    @router.get('/api/research/loops')
    async def history(request:Request,response:Response):
        owner=authorize(request);response.headers['Cache-Control']='no-store'
        try:return {'items':[view(*row) for row in records(storage(),owner)]}
        except Exception as exc:raise error(exc)
    @router.get('/api/research/loops/{identifier}')
    async def retrieve(identifier:str,request:Request,response:Response):
        owner=authorize(request);response.headers['Cache-Control']='no-store'
        try:return get(storage(),owner,identifier)
        except Exception as exc:raise error(exc)
    async def execute(owner,identifier,*,resume=False,revision=None,pause=False):
        if owner in active or len(active)>=4:raise HTTPException(429,'已有循环运行，请稍后重试。')
        active.add(owner)
        async def checkpoint(snapshot):
            if pause and snapshot['last_node']=='execute':raise Paused()
        try:
            await run_durable('Find the synthetic disease and read its target profile.',OfflineModel(),synthetic_tool,
                path=storage(),owner=owner,identifier=identifier,contract='synthetic-http-v1',resume=resume,
                expected_revision=revision,limits=Limits(seconds=120),on_state=checkpoint)
        except Paused:pass
        except Exception as exc:raise error(exc)
        finally:active.discard(owner)
        return get(storage(),owner,identifier)
    @router.post('/api/research/loops',status_code=201)
    async def create(body:Create,request:Request,response:Response):
        owner=authorize(request);response.headers['Cache-Control']='no-store'
        return await execute(owner,uuid4().hex,pause=body.pause_after_observation)
    @router.post('/api/research/loops/{identifier}/resume')
    async def resume(identifier:str,body:Revision,request:Request,response:Response):
        owner=authorize(request);response.headers['Cache-Control']='no-store'
        try:get(storage(),owner,identifier)
        except Exception as exc:raise error(exc)
        return await execute(owner,identifier,resume=True,revision=body.revision)
    @router.delete('/api/research/loops/{identifier}')
    async def remove(identifier:str,body:Revision,request:Request,response:Response):
        owner=authorize(request);response.headers['Cache-Control']='no-store'
        try:delete(storage(),owner,identifier,body.revision);return {'status':'deleted'}
        except Exception as exc:raise error(exc)
    return router
