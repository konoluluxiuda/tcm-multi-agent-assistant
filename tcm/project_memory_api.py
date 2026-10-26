"""Local, explicit project selection; no model inference or external transmission."""
import hashlib
import os
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field, BaseModel, field_validator
from tcm.project_memory import ProjectData, ProjectStore, ProjectConflict
from tcm.research_run_api import enabled


class UpdateProject(ProjectData):
    revision: int = Field(ge=1)


class RecallQuery(BaseModel):
    model_config = {'extra':'forbid'}
    query: str = Field(min_length=1, max_length=400)

    @field_validator('query')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('Blank query')
        return value.strip()


class ApplyQuery(RecallQuery):
    revision: int = Field(ge=1, strict=True)


def create_router(memory_provider, cookie_name):
    router = APIRouter()

    def context(request, response):
        response.headers['Cache-Control'] = 'no-store'
        if not enabled():
            raise HTTPException(404, '项目记忆未启用。')
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD') and (
            request.headers.get('sec-fetch-site') == 'cross-site' or
            origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.url.scheme, request.url.netloc)
        ):
            raise HTTPException(403, '请从本站页面操作。')
        memory = memory_provider()
        cookie = request.cookies.get(cookie_name())
        if not memory.valid_cookie(cookie):
            raise HTTPException(401, '请刷新聊天页面初始化会话。')
        path = os.getenv('RESEARCH_PROJECT_DB') or Path(memory.path).with_name('research_projects.sqlite3')
        return ProjectStore(path), hashlib.sha256(cookie.encode()).hexdigest()

    def invoke(action):
        try:
            return action()
        except KeyError:
            raise HTTPException(404, '项目不存在或已删除。')
        except ProjectConflict as exc:
            raise HTTPException(409, str(exc))
        except ValueError:
            raise HTTPException(409, '资料目录已变化或请求无法解析，请在报告表单明确填写。')
        except (OSError, sqlite3.Error):
            raise HTTPException(503, '项目存储暂不可用。')

    @router.get('/api/research/projects')
    def listing(request: Request, response: Response):
        store, owner = context(request, response)
        return invoke(lambda: {'items': store.list(owner)})

    @router.post('/api/research/projects', status_code=201)
    def create(body: ProjectData, request: Request, response: Response):
        store, owner = context(request, response)
        return invoke(lambda: store.save(owner, body.model_dump()))

    @router.post('/api/research/projects/recall')
    def semantic_recall(body: RecallQuery, request: Request, response: Response):
        from tcm.project_semantic import recall as search
        store, owner = context(request, response)
        return invoke(lambda: search(store, owner, body.query))

    @router.post('/api/research/projects/{identifier}/apply')
    def apply(body: ApplyQuery, identifier: str, request: Request, response: Response):
        from tcm.project_semantic import apply_context
        store, owner = context(request, response)
        return invoke(lambda: apply_context(store, owner, identifier, body.revision, body.query))

    @router.get('/api/research/projects/{identifier}')
    def recall(identifier: str, request: Request, response: Response):
        store, owner = context(request, response)
        return invoke(lambda: store.get(owner, identifier))

    @router.put('/api/research/projects/{identifier}')
    def update(identifier: str, body: UpdateProject, request: Request, response: Response):
        store, owner = context(request, response)
        return invoke(lambda: store.save(owner, body.model_dump(exclude={'revision'}), identifier=identifier, revision=body.revision))

    @router.delete('/api/research/projects/{identifier}')
    def delete(identifier: str, request: Request, response: Response, revision: int = Query(ge=1)):
        store, owner = context(request, response)
        return invoke(lambda: (store.delete(owner, identifier, revision), {'deleted': True})[1])

    return router
