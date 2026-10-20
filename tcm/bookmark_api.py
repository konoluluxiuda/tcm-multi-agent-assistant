"""Anonymous, browser-owned bookmarks; all replay stays in local source tools."""
from pathlib import Path
import os
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from tcm.bookmark_backup import MAX_BYTES, InvalidBookmarkBackup

from agents.runtime import session_turn
from features import env_bool
from tcm.mineru_research import SnapshotChanged
from tcm.research_bookmarks import (BookmarkStore, BookmarkUnavailable, BookmarkConflict,
    BookmarkMissing, BookmarkLimit, BookmarkStale, capture, check_source, preview)


def enabled():
    return env_bool('ENABLE_RESEARCH_BOOKMARKS', env_bool('RESEARCH_TOOLS_ONLY'))


class SaveBookmark(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_revision: int = Field(strict=True, ge=0)
    title: str = Field(default='', max_length=120)
    note: str = Field(default='', max_length=2000)


class EditBookmark(BaseModel):
    model_config = {'extra': 'forbid'}
    revision: int = Field(strict=True, ge=1)
    title: str = Field(min_length=1, max_length=120)
    note: str = Field(default='', max_length=2000)


def create_router(memory_provider, cookie_name, research_runtime):
    router = APIRouter()

    def storage():
        if not enabled():
            raise HTTPException(404, '研究收藏未启用。')
        try:
            memory = memory_provider()
            path = Path(os.getenv('RESEARCH_BOOKMARK_DB') or memory.path.with_name('research_bookmarks.sqlite3')).resolve()
            if path == memory.path:
                raise BookmarkUnavailable()
            yield BookmarkStore(path)
        except BookmarkMissing:
            raise HTTPException(404, '未找到本浏览器的收藏。', headers={'Cache-Control': 'no-store'})
        except BookmarkConflict:
            raise HTTPException(409, '收藏已被其他页面修改，请刷新列表后重试。', headers={'Cache-Control': 'no-store'})
        except BookmarkLimit:
            raise HTTPException(409, '每个浏览器最多保存100项收藏，请先删除不需要的项目。')
        except BookmarkStale:
            raise HTTPException(409, '收藏来源或查询规则已经变化；请重新查询并收藏，不会自动替换原来源。')
        except InvalidBookmarkBackup:
            raise HTTPException(422, '收藏备份格式、版本或完整性校验不通过，未导入任何项目。')
        except (BookmarkUnavailable, SnapshotChanged, OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            raise HTTPException(503, '收藏存储或本地来源暂不可用，未调用模型或联网补答。', headers={'Cache-Control': 'no-store'})

    def owner(request, response, store, *, create=False):
        name = cookie_name() + '_bookmarks'
        cookie = request.cookies.get(name)
        if not store.valid_cookie(cookie):
            if not create:
                raise HTTPException(401, '请先打开研究收藏，再执行保存或修改。')
            cookie = store.new_cookie()
        response.set_cookie(name, cookie, httponly=True, samesite='lax',
                            max_age=365*86400, secure=env_bool('CHAT_COOKIE_SECURE'))
        response.headers['Cache-Control'] = 'private, no-store'
        return cookie

    def current(request):
        memory = memory_provider()
        cookie = request.cookies.get(cookie_name())
        if not memory.valid_cookie(cookie):
            return None
        return memory.load_serialized(cookie)

    @router.get('/research-bookmarks.js')
    def javascript(store=Depends(storage)):
        return FileResponse(Path(__file__).with_name('research_bookmarks.js'), media_type='text/javascript',
                            headers={'Cache-Control': 'no-store'})

    @router.get('/api/research/bookmarks')
    def listing(request: Request, response: Response, offset: int = Query(0, ge=0, le=100), store=Depends(storage)):
        cookie = owner(request, response, store, create=True)
        result = store.list(cookie, offset=offset)
        from agents.conversation_store import SessionExpired
        try:
            snapshot = current(request)
        except SessionExpired:
            snapshot = None
        payload = capture(snapshot.tcm_context) if snapshot else None
        summary = None
        if payload:
            focus = payload['focus']
            summary = ('药典 · ' + focus['last_question'] if payload['kind'] == 'research' else
                       'ETCM · ' + focus['query_kind'] + ' · 第' + str(focus['page']) + '页')
        return {**result, 'can_save': payload is not None, 'current_summary': summary,
                'conversation_revision': snapshot.revision if snapshot else None}

    @router.post('/api/research/bookmarks')
    def save(body: SaveBookmark, request: Request, response: Response, store=Depends(storage)):
        cookie = owner(request, response, store)
        session = request.cookies.get(cookie_name())
        if not memory_provider().valid_cookie(session):
            raise HTTPException(409, '请先完成一次药典或关联查询。')
        with session_turn(session):
            snapshot = current(request)
            if snapshot.revision != body.expected_revision:
                raise HTTPException(409, '当前聊天已更新，请刷新收藏区再保存，避免收藏了另一项查询。')
            payload = capture(snapshot.tcm_context)
            if payload is None:
                raise HTTPException(409, '当前没有可收藏的药典或已选来源关联查询。')
            check_source(payload, research_runtime)
            focus = payload['focus']
            default = ('药典 · ' + focus['last_question'] if payload['kind'] == 'research' else
                       'ETCM · ' + focus['query_kind'] + ' · 第' + str(focus['page']) + '页')
            item, created = store.create(cookie, body.title.strip() or default[:120], body.note, payload)
        return {'item': item, 'created': created}

    @router.get('/api/research/bookmarks/export')
    def export(request: Request, response: Response, store=Depends(storage)):
        cookie = owner(request, response, store)
        result = Response(store.export_backup(cookie), media_type='application/json', headers={
            'Cache-Control': 'private, no-store',
            'Content-Disposition': 'attachment; filename="tcm-research-bookmarks-v1.json"'})
        result.headers['set-cookie'] = response.headers['set-cookie']
        return result

    @router.post('/api/research/bookmarks/import')
    async def restore(request: Request, response: Response, store=Depends(storage)):
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise HTTPException(415, '请选择JSON格式的收藏备份。')
        data = bytearray()
        async for part in request.stream():
            if len(data) + len(part) > MAX_BYTES:
                raise HTTPException(413, '收藏备份不能超过2 MiB，未导入任何项目。')
            data.extend(part)
        cookie = await run_in_threadpool(owner, request, response, store)
        result = await run_in_threadpool(store.import_backup, cookie, bytes(data))
        return {**result, 'message': '导入完成；重复查询保留已有笔记。来源在预览时重新核验。'}

    @router.patch('/api/research/bookmarks/{identifier}')
    def edit(identifier: str, body: EditBookmark, request: Request, response: Response, store=Depends(storage)):
        cookie = owner(request, response, store)
        if not body.title.strip():
            raise HTTPException(422, '标题不能为空白。')
        return {'item': store.update(cookie, identifier, body.revision, body.title.strip(), body.note)}

    @router.delete('/api/research/bookmarks/{identifier}')
    def delete(identifier: str, request: Request, response: Response,
               revision: int = Query(..., ge=1), store=Depends(storage)):
        cookie = owner(request, response, store)
        store.delete(cookie, identifier, revision)
        return {'status': 'deleted'}

    @router.post('/api/research/bookmarks/{identifier}/preview')
    def replay(identifier: str, request: Request, response: Response, store=Depends(storage)):
        cookie = owner(request, response, store)
        item = store.get(cookie, identifier)
        return {'item': {k: v for k, v in item.items() if k != 'payload'},
                **preview(item['payload'], research_runtime), 'llm_calls': 0, 'network_calls': 0}

    @router.get('/api/research/bookmarks/{identifier}/report')
    def report(identifier: str, request: Request, response: Response, revision: int = Query(..., ge=1),
               include_note: bool = False, store=Depends(storage)):
        from tcm.bookmark_report import export_note
        cookie = owner(request, response, store)
        item = store.get(cookie, identifier)
        if item['revision'] != revision:
            raise BookmarkConflict()
        content = export_note(item, research_runtime, include_note=include_note)
        if store.get(cookie, identifier)['revision'] != revision:
            raise BookmarkConflict()
        result = Response(content.encode('utf-8'), media_type='text/markdown; charset=utf-8', headers={
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': 'attachment; filename="tcm-bookmark-evidence.md"'})
        result.headers['set-cookie'] = response.headers['set-cookie']
        return result

    return router
