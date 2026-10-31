"""Opt-in RAG page/API; session ownership, origin checks and durable no-resend."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from features import env_bool
from tcm.dataops.api import validate_origin
from tcm.request_journal import RequestJournal as Runs


class Question(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question:str=Field(min_length=1,max_length=500)
    request_key:str=Field(pattern=r'^[A-Za-z0-9_-]{8,100}$')
    compose:bool=False


def enabled():return env_bool('ENABLE_RAG_WORKBENCH')


def generate_enabled():
    return env_bool('ENABLE_RAG_GENERATION') and all(os.getenv(k) for k in ('RAG_MODEL','RAG_BASE_URL','RAG_API_KEY'))


def answer(question,compose):
    from tcm.rag_retrieval import ReleaseSearch
    from tcm.rag_pipeline import RagPipeline
    from tcm.rag_model import BoundedChat
    search=ReleaseSearch(os.environ['RAG_RELEASE_PATH'])
    try:return RagPipeline(search,BoundedChat() if compose and generate_enabled() else None).run(question,compose)
    finally:search.close()


def create_router(memory_provider,cookie_name,*,runner=answer):
    router=APIRouter()
    @router.get('/rag',response_class=HTMLResponse)
    def page(request:Request):
        if not enabled():raise HTTPException(404,'引用资料页未启用。')
        memory=memory_provider()
        response=HTMLResponse(Path(__file__).with_name('rag_workbench.html').read_text(),headers={'Cache-Control':'no-store'})
        if not memory.valid_cookie(request.cookies.get(cookie_name())):
            response.set_cookie(cookie_name(),memory.new_cookie(),httponly=True,samesite='lax',
                                secure=env_bool('CHAT_COOKIE_SECURE'),max_age=memory.ttl_seconds)
        return response

    def context(request,response):
        response.headers['Cache-Control']='no-store'
        if not enabled():raise HTTPException(404,'引用资料页未启用。')
        validate_origin(request)
        cookie=request.cookies.get(cookie_name())
        if not memory_provider().valid_cookie(cookie):raise HTTPException(401,'请先打开引用资料页初始化会话。')
        return hashlib.sha256(cookie.encode()).hexdigest()

    @router.get('/api/rag/capabilities')
    def capabilities(request:Request,response:Response):
        context(request,response)
        return {'enabled':True,'generation_enabled':generate_enabled(),
                'generation_mode':'literal_extract_and_compare','request_limit':1}

    @router.post('/api/rag/answers')
    def query(body:Question,request:Request,response:Response):
        owner=context(request,response)
        if not body.question.strip():raise HTTPException(422,'请输入问题。')
        try:
            store=Runs(os.getenv('RAG_RUN_DB') or Path(memory_provider().path).with_name('rag_answers.sqlite3'))
        except (OSError,sqlite3.Error):raise HTTPException(503,'回答记录暂不可用，未调用模型。') from None
        signature=hashlib.sha256(json.dumps({'question':body.question,'compose':body.compose,
                    'release':os.getenv('RAG_RELEASE_PATH'),'generation':generate_enabled(),
                    'model':os.getenv('RAG_MODEL'),'recipient':os.getenv('RAG_BASE_URL')},sort_keys=True).encode()).hexdigest()
        cached=store.reserve(owner,body.request_key,signature)
        if cached is not None:return {**cached,'replayed':True}
        try:
            result=runner(body.question,body.compose)
        except Exception:
            # Unknown dispatch stays reserved; no exception text/credentials are
            # returned and the same request key cannot trigger another attempt.
            raise HTTPException(503,'资料或回答服务暂不可用；本请求未自动重试。') from None
        try:store.complete(owner,body.request_key,result)
        except (OSError,sqlite3.Error):raise HTTPException(503,'结果保存失败；请求状态已保留，未自动重试。') from None
        return {**result,'replayed':False}
    return router
