"""Lightweight read-only HTTP API over the ETCM herb repository.

Run independently of app.py: this module imports only tcm.herbs (pure stdlib),
so it does not load the agent graph or any CV model.

    uvicorn tcm.api:app --port 7860

Config via env: TCM_DB (default var/tcm/herbs.sqlite3), TCM_DATASET_VERSION (default latest).
"""
import html
import json
import os
import sqlite3

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .herbs import compare_herbs, filter_herbs, lookup

DB_PATH = os.environ.get('TCM_DB', 'var/tcm/herbs.sqlite3')
DATASET_VERSION = os.environ.get('TCM_DATASET_VERSION') or None

app = FastAPI(title='TCM Herb Query API', version='1.0')

FILTER_FIELDS = ('family', 'habitat', 'medicinal_part', 'properties',
                 'flavors', 'meridians', 'efficacy_class', 'indications')


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


@app.get('/herbs/compare')
def compare(names: str = Query(..., description='Comma-separated herb names')):
    return _call(compare_herbs, DB_PATH, names=[n.strip() for n in names.split(',') if n.strip()],
                 dataset_version=DATASET_VERSION)


@app.get('/herbs/{name}')
def herb_detail(name: str):
    return _call(lookup, DB_PATH, name=name, dataset_version=DATASET_VERSION)


@app.get('/herbs')
def filter_query(
    family: str = None, habitat: str = None, medicinal_part: str = None,
    properties: str = None, flavors: str = None, meridians: str = None,
    efficacy_class: str = None, indications: str = None,
):
    filters = {k: v for k, v in {
        'family': family, 'habitat': habitat, 'medicinal_part': medicinal_part,
        'properties': properties, 'flavors': flavors, 'meridians': meridians,
        'efficacy_class': efficacy_class, 'indications': indications,
    }.items() if v}
    if not filters:
        return JSONResponse(status_code=400, content={'status': 'error', 'error': 'provide at least one filter field'})
    return _call(filter_herbs, DB_PATH, filters=filters, dataset_version=DATASET_VERSION)


_INDEX_HTML = """<!doctype html>
<meta charset="utf-8">
<title>TCM Herb Query</title>
<style>
 body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2rem auto; padding: 0 1rem; }
 input, button { font: inherit; padding: .4rem .6rem; }
 .row { margin: .5rem 0; }
 pre { background: #f6f8fa; padding: 1rem; border-radius: 6px; overflow: auto; }
 code { font-family: ui-monospace, monospace; font-size: 13px; }
</style>
<h1>TCM Herb Query</h1>
<p>按名称精确查询、按字段筛选、多药材比较（拼音 / 拉丁名 / 英文名）。</p>

<div class="row">
  <input id="name" placeholder="药材名，如 HuangQin">
  <button onclick="get('/herbs/'+encodeURIComponent(name.value))">查详情</button>
</div>
<div class="row">
  <input id="filterField" placeholder="字段，如 family" style="width:12em">
  <input id="filterVal" placeholder="值，如 Leguminosae">
  <button onclick="get('/herbs?'+encodeURIComponent(filterField.value)+'='+encodeURIComponent(filterVal.value))">筛选</button>
</div>
<div class="row">
  <input id="names" placeholder="比较，逗号分隔，如 HuangQin,HuaShi" style="width:30em">
  <button onclick="get('/herbs/compare?names='+encodeURIComponent(names.value))">比较</button>
</div>

<pre id="out"><code>结果将显示在这里</code></pre>

<script>
async function get(path) {
  const out = document.getElementById('out');
  out.innerHTML = '<code>加载中…</code>';
  try {
    const res = await fetch(path);
    const data = await res.json();
    out.innerHTML = '<code>' + escapeHtml(JSON.stringify(data, null, 2)) + '</code>';
  } catch (e) {
    out.innerHTML = '<code>' + escapeHtml(String(e)) + '</code>';
  }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
</script>
"""


@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    return HTMLResponse(_INDEX_HTML)
