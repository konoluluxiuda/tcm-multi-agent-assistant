"""Request-bound local research with durable owner-scoped audit records."""
import asyncio
import hashlib
from contextlib import suppress
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from features import env_bool
from tcm.research_run_local import run_local_profile
from tcm.target_similarity import SnapshotChanged
from tcm.research_run_store import RunStore, RunConflict


def enabled():
    return env_bool('ENABLE_RESEARCH_TASKS') and bool(os.getenv('ETCM_DISEASE_DB'))


class ProfileRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    disease: str = Field(min_length=1, max_length=200)

    @field_validator('disease')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Disease name is required')
        return value.strip()


def create_router(memory_provider, cookie_name):
    router = APIRouter()
    active = set()

    @router.get('/research-runs.js')
    def javascript():
        return FileResponse(Path(__file__).with_name('research_runs.js'), media_type='application/javascript')

    def authorize(request):
        if not enabled():
            raise HTTPException(404, '本地研究入口未启用。')
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD') and (request.headers.get('sec-fetch-site') == 'cross-site' or
                (origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) !=
                 (request.url.scheme, request.url.netloc))):
            raise HTTPException(403, '请从本站聊天页发起查询。')
        owner = request.cookies.get(cookie_name())
        if not memory_provider().valid_cookie(owner):
            raise HTTPException(401, '请刷新聊天页初始化会话。')
        return hashlib.sha256(owner.encode()).hexdigest()

    def storage():
        configured=os.getenv('RESEARCH_RUN_DB')
        path=Path(configured) if configured else memory_provider().path.with_name('research_runs.sqlite3')
        return RunStore(path)

    @router.get('/api/research/runs')
    async def history(request: Request, response: Response):
        response.headers['Cache-Control']='no-store'
        owner=authorize(request)
        try:
            rows=storage().list(owner)
            return {'items':[{k:r[k] for k in ('id','revision','question','status','updated')} for r in rows]}
        except Exception:raise HTTPException(503,'研究记录暂不可用。')

    @router.get('/api/research/runs/{identifier}')
    async def retrieve(identifier: str, request: Request, response: Response):
        response.headers['Cache-Control']='no-store'
        owner=authorize(request)
        try:return storage().get(owner,identifier)
        except KeyError:raise HTTPException(404,'未找到本会话的研究记录。')
        except Exception:raise HTTPException(503,'研究记录暂不可用。')

    @router.delete('/api/research/runs/{identifier}')
    async def delete(identifier: str, request: Request):
        owner=authorize(request)
        try:
            storage().delete(owner,identifier)
            return {'status':'deleted'}
        except KeyError:raise HTTPException(404,'未找到本会话的研究记录。')
        except RunConflict:raise HTTPException(409,'运行中记录不能删除；请先取消，或由维护者处理过期记录。')
        except Exception:raise HTTPException(503,'研究记录暂不可用。')

    @router.post('/api/research/runs/profile')
    async def profile(body: ProfileRequest, request: Request):
        owner=authorize(request)
        if owner in active or len(active) >= 4:
            raise HTTPException(429, '已有研究查询正在执行，请稍后重试。')
        try:
            store=storage()
            doc=store.create(owner,body.disease)
        except Exception:raise HTTPException(503,'无法保存研究记录，查询尚未开始。')
        active.add(owner)
        async def persist(snapshot):
            nonlocal doc
            doc=store.save(owner,doc,snapshot=snapshot)
        async def execute():
            nonlocal doc
            result=await run_local_profile(os.environ['ETCM_DISEASE_DB'],body.disease,on_state=persist)
            doc=store.save(owner,doc,status=result['status'],result=result)
            return {'run_id':doc['id'],**result}
        task = asyncio.create_task(execute())

        async def watch_disconnect():
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    return
                await asyncio.sleep(0.1)

        watcher = asyncio.create_task(watch_disconnect())
        try:
            return await task
        except SnapshotChanged:
            store.save(owner,doc,status='failed')
            raise HTTPException(409, '资料已更新，请重新查询。')
        except asyncio.CancelledError:
            with suppress(Exception):store.save(owner,doc,status='cancelled')
            raise
        except Exception:
            with suppress(Exception):store.save(owner,doc,status='failed')
            raise HTTPException(503, '本地资料暂不可用，请稍后重试。')
        finally:
            watcher.cancel()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            with suppress(asyncio.CancelledError, Exception):
                await task
            active.discard(owner)

    return router
