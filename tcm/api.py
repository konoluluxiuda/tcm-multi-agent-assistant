"""Lightweight read-only HTTP API over the ETCM herb repository.

Run independently of app.py: this module imports only tcm.herbs (pure stdlib),
so it does not load the agent graph or any CV model.

    uvicorn tcm.api:app --host 127.0.0.1 --port 7863

Config via env: TCM_DB (default var/tcm/herbs.sqlite3), TCM_DATASET_VERSION (default latest).
"""
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .herbs import FILTERABLE_FIELDS, compare_herbs, dataset_status, filter_herbs, lookup

DB_PATH = os.environ.get('TCM_DB', 'var/tcm/herbs.sqlite3')
DATASET_VERSION = os.environ.get('TCM_DATASET_VERSION') or None

app = FastAPI(title='TCM Herb Query API', version='1.0')

FILTER_FIELDS = FILTERABLE_FIELDS


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={'status': 'error', 'error': str(exc)})
    except (sqlite3.OperationalError, FileNotFoundError) as exc:
        return JSONResponse(status_code=503, content={'status': 'error', 'error': str(exc)})


@app.get('/health')
def health():
    return {'status': 'healthy'}


@app.get('/ready')
def ready():
    try:
        result = dataset_status(DB_PATH, dataset_version=DATASET_VERSION)
    except (sqlite3.Error, OSError, ValueError):
        return JSONResponse(status_code=503, content={'status': 'database_unavailable'})
    return JSONResponse(status_code=200 if result['status'] == 'ready' else 503, content=result)


@app.get('/herbs/compare')
def compare(names: str = Query(..., description='Comma-separated herb names'), dataset_version: str = None):
    return _call(compare_herbs, DB_PATH, names=[n.strip() for n in names.split(',') if n.strip()],
                 dataset_version=dataset_version or DATASET_VERSION)


@app.get('/herb-records/{source_record_id}')
def herb_record(source_record_id: str, dataset_version: str = None):
    return _call(lookup, DB_PATH, source_record_id=source_record_id,
                 dataset_version=dataset_version or DATASET_VERSION)


@app.get('/herbs/{name}')
def herb_detail(name: str, dataset_version: str = None):
    return _call(lookup, DB_PATH, name=name, dataset_version=dataset_version or DATASET_VERSION)


@app.get('/herbs')
def filter_query(
    request: Request,
    family: str = None, habitat: str = None, medicinal_part: str = None,
    properties: str = None, flavors: str = None, meridians: str = None,
    efficacy_class: str = None, indications: str = None,
    limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
    dataset_version: str = None,
):
    supplied = list(request.query_params.keys())
    unknown = set(supplied) - FILTER_FIELDS - {'limit', 'offset', 'dataset_version'}
    if unknown:
        return JSONResponse(status_code=400, content={'status': 'error', 'error': 'unsupported_filter_field: ' + ', '.join(sorted(unknown))})
    if any(len(request.query_params.getlist(k)) != 1 for k in supplied):
        return JSONResponse(status_code=400, content={'status': 'error', 'error': 'duplicate_filter_field'})
    filters = {k: v for k, v in {
        'family': family, 'habitat': habitat, 'medicinal_part': medicinal_part,
        'properties': properties, 'flavors': flavors, 'meridians': meridians,
        'efficacy_class': efficacy_class, 'indications': indications,
    }.items() if v is not None}
    if not filters:
        return JSONResponse(status_code=400, content={'status': 'error', 'error': 'provide at least one filter field'})
    return _call(filter_herbs, DB_PATH, filters=filters, dataset_version=dataset_version or DATASET_VERSION,
                 limit=limit, offset=offset)


@app.get('/', response_class=HTMLResponse)
def index():
    return HTMLResponse(Path(__file__).with_name('web.html').read_text(encoding='utf-8'))
