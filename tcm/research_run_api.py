"""Request-bound local research with durable owner-scoped audit records."""
import asyncio
import hashlib
from contextlib import suppress
import os
import sqlite3
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from features import env_bool
from tcm.research_run_local import run_local_profile, run_local_actions
from tcm.research_run_scope import RunScope
from tcm.target_similarity import SnapshotChanged
from tcm.research_run_store import RunStore, RunConflict
from tcm.research_evidence_store import for_run, canonical, EvidenceConflict
from tcm.research_evidence_read import ReadEvidence, read as read_evidence


def enabled():
    return env_bool('ENABLE_RESEARCH_TASKS')


class ProfileRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    disease: str = Field(min_length=1, max_length=200)

    @field_validator('disease')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Disease name is required')
        return value.strip()


class ActionsRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    scope: RunScope


def create_router(memory_provider, cookie_name):
    router = APIRouter()
    active = set()

    @router.get('/research-plan.js')
    def plan_javascript():
        return FileResponse(Path(__file__).with_name('research_plan.js'), media_type='application/javascript')

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

    def verified(owner,identifier):
        store=storage()
        try:doc=store.get(owner,identifier)
        except KeyError:
            from tcm.research_plan_jobs import get as get_plan
            try:doc=get_plan(owner,identifier,store.path)
            except Exception as exc:
                from tcm.research_jobs import JobConflict
                if isinstance(exc,JobConflict):raise EvidenceConflict('Job source changed') from None
                raise
        result=doc.get('result') or {}
        evidence=for_run(store.path,owner,identifier)
        contract=result.get('source_contract')
        if contract:
            from tcm.research_run_local import LocalAnalysisTools
            keys=contract['databases']
            try:
                backend=LocalAnalysisTools(os.getenv('ETCM_DISEASE_DB') if 'ETCM_DISEASE_DB' in keys else None,
                    herbs=os.getenv('TCM_DB') if 'TCM_DB' in keys else None,
                    enrichment=os.getenv('ETCM_ENRICHMENT_DB') if 'ETCM_ENRICHMENT_DB' in keys else None,
                    pharmacopoeia=os.getenv('PHARMACOPOEIA_INDEX') if contract.get('pharmacopoeia') else None)
            except (OSError,ValueError,sqlite3.Error):raise EvidenceConflict('Source unavailable') from None
            if canonical(backend.contract)!=canonical(contract):raise EvidenceConflict('Source changed')
        with evidence.store.activity():evidence.view(result.get('run',{}).get('evidence',{}))
        return doc,evidence

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
        try:return verified(owner,identifier)[0]
        except KeyError:raise HTTPException(404,'未找到本会话的研究记录。')
        except (EvidenceConflict,SnapshotChanged):raise HTTPException(409,'来源或完整证据已改变，请重新核对。')
        except Exception:raise HTTPException(503,'研究记录暂不可用。')

    @router.get('/api/research/runs/{identifier}/evidence/{evidence_id}')
    async def read(identifier:str,evidence_id:str,request:Request,response:Response):
        owner=authorize(request);response.headers['Cache-Control']='no-store'
        try:
            if 'evidence_id' in request.query_params:raise ValueError('Path identity is fixed')
            values=dict(request.query_params)
            for key in ('offset','limit','item_index'):
                if key in values:
                    if not re.fullmatch(r'0|[1-9][0-9]{0,5}',values[key]):raise ValueError('Invalid integer')
                    values[key]=int(values[key])
            args=ReadEvidence.model_validate({'evidence_id':evidence_id,**values})
        except ValueError:raise HTTPException(422,'证据读取参数无效。')
        try:
            doc,evidence=verified(owner,identifier)
            payload=(doc.get('result') or {}).get('run',{}).get('evidence',{}).get(evidence_id)
            if payload is None:raise KeyError(evidence_id)
            with evidence.store.activity():
                full=evidence.resolve(evidence_id,payload)
                result=read_evidence({evidence_id:full},args.model_dump())
                if payload.get('externalized') and result.get('data'):
                    result['data'].update(evidence_handle=payload['evidence_handle'],content_sha256=payload['content_sha256'])
            return result
        except KeyError:raise HTTPException(404,'未找到本会话的证据。')
        except (EvidenceConflict,SnapshotChanged):raise HTTPException(409,'来源或完整证据已改变，请重新核对。')
        except Exception:raise HTTPException(503,'证据暂不可用。')

    @router.get('/api/research/runs/{identifier}/evidence/{evidence_id}/download')
    async def download(identifier:str,evidence_id:str,request:Request):
        owner=authorize(request)
        try:ReadEvidence.model_validate({'evidence_id':evidence_id,'field':'rows'})
        except ValueError:raise HTTPException(422,'证据标识无效。')
        try:
            doc,evidence=verified(owner,identifier)
            payload=(doc.get('result') or {}).get('run',{}).get('evidence',{}).get(evidence_id)
            if payload is None:raise KeyError(evidence_id)
            with evidence.store.activity():raw=canonical(evidence.resolve(evidence_id,payload))
            return Response(raw,media_type='application/json',headers={
                'Cache-Control':'no-store','Content-Disposition':f'attachment; filename="{evidence_id}.json"',
                'X-Content-Type-Options':'nosniff'})
        except KeyError:raise HTTPException(404,'未找到本会话的证据。')
        except (EvidenceConflict,SnapshotChanged):raise HTTPException(409,'来源或完整证据已改变，请重新核对。')
        except Exception:raise HTTPException(503,'证据暂不可用。')

    @router.delete('/api/research/runs/{identifier}')
    async def delete(identifier: str, request: Request):
        owner=authorize(request)
        try:
            store=storage();doc=store.get(owner,identifier)
            if doc['status']=='running':raise RunConflict('Active run')
            evidence=for_run(store.path,owner,identifier)
            with evidence.store.activity(exclusive=True):
                store.delete(owner,identifier)
                with sqlite3.connect(evidence.store.path) as db:
                    db.execute('DELETE FROM evidence WHERE owner=? AND run_id=?',(owner,identifier))
            return {'status':'deleted'}
        except KeyError:raise HTTPException(404,'未找到本会话的研究记录。')
        except RunConflict:raise HTTPException(409,'运行中记录不能删除；请先取消，或由维护者处理过期记录。')
        except Exception:raise HTTPException(503,'研究记录暂不可用。')

    async def execute_request(request, question, execute_plan):
        owner=authorize(request)
        if owner in active or len(active) >= 4:
            raise HTTPException(429, '已有研究查询正在执行，请稍后重试。')
        try:
            store=storage()
            doc=store.create(owner,question)
        except Exception:raise HTTPException(503,'无法保存研究记录，查询尚未开始。')
        active.add(owner)
        async def persist(snapshot):
            nonlocal doc
            doc=store.save(owner,doc,snapshot=snapshot)
        async def execute():
            nonlocal doc
            result=await execute_plan(persist,for_run(store.path,owner,doc['id']))
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

    @router.post('/api/research/runs/profile')
    async def profile(body: ProfileRequest, request: Request):
        if not os.getenv('ETCM_DISEASE_DB'):raise HTTPException(404,'疾病索引尚未启用。')
        async def execute(persist,evidence):
            return await run_local_profile(os.environ['ETCM_DISEASE_DB'],body.disease,on_state=persist,evidence_store=evidence)
        return await execute_request(request,body.disease,execute)

    @router.post('/api/research/runs/actions')
    async def actions(body: ActionsRequest, request: Request):
        if any(r.call['tool'] in ('pharmacopoeia_lookup','pair_evidence_report') for r in body.scope.requirements) and not env_bool('ENABLE_PHARMACOPOEIA_RESEARCH'):
            raise HTTPException(404,'药典研究工具尚未启用。')
        async def execute(persist,evidence):
            return await run_local_actions(os.getenv('ETCM_DISEASE_DB'),body.scope,
                herbs=os.getenv('TCM_DB'),enrichment=os.getenv('ETCM_ENRICHMENT_DB'),on_state=persist,evidence_store=evidence,
                pharmacopoeia=os.getenv('PHARMACOPOEIA_INDEX'))
        return await execute_request(request,'本地分析：'+', '.join(r.id for r in body.scope.requirements),execute)

    return router
