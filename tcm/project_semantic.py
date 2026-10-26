"""Local-only semantic candidates. SQLite remains the authority for every result."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import threading
import time

from tcm.project_memory import ProjectConflict, ProjectStore

# Bound concurrent CPU workers across this HTTP process; no external provider fallback.
SLOTS = threading.BoundedSemaphore(2)


def enabled():
    return bool(os.getenv('PROJECT_MEMORY_PYTHON') and os.getenv('PROJECT_MEMORY_MODEL'))


def memory_reference(query):
    return bool(re.search(r'(?:上次|之前|先前|以前|那个|那组|那两味|原来).{0,40}(?:项目|研究|整理|比较|药|继续)|(?:继续|接着|回到|找回|找一下).{0,30}(?:上次|之前|先前|那组|那个|研究项目)', query))


def store_for(memory):
    return ProjectStore(os.getenv('RESEARCH_PROJECT_DB') or Path(memory.path).with_name('research_projects.sqlite3'))


def owner_key(cookie):
    return hashlib.sha256(cookie.encode()).hexdigest()


def lexical_score(query, document):
    def grams(s):
        s = re.sub(r'\W', '', s.lower())
        return {s[i:i+2] for i in range(len(s)-1)}
    q, d = grams(query), grams(document)
    return len(q & d) / max(1, len(q))


def document_text(doc):
    return doc['title'] + '；药材：' + '、'.join(doc['herbs']) + '；关注问题：' + doc['question']


def local_rank(query, docs):
    if not enabled():
        raise RuntimeError('Local semantic memory is disabled')
    payload = {'query': query, 'documents': [{'id':d['id'], 'text':document_text(d)} for d in docs]}
    env = {k: os.environ[k] for k in ('PATH','HOME','LANG','SYSTEMROOT','TEMP','TMP') if k in os.environ}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    if not SLOTS.acquire(timeout=1):
        raise RuntimeError('Memory worker busy')
    try:
        result = subprocess.run([os.environ['PROJECT_MEMORY_PYTHON'], '-m', 'scripts.project_memory_dense',
                                 '--model', os.environ['PROJECT_MEMORY_MODEL']],
                                input=json.dumps(payload, ensure_ascii=False), text=True,
                                capture_output=True, timeout=25, env=env,
                                cwd=Path(__file__).resolve().parents[1], check=True)
    finally:
        SLOTS.release()
    if len(result.stdout) > 32000:
        raise ValueError('Invalid local worker response')
    data = json.loads(result.stdout)
    if data.get('truncated'):
        raise ValueError('Memory input exceeds local model token limit')
    scores = data['scores']
    if set(scores) != {d['id'] for d in docs} or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not -1.01<=v<=1.01 for v in scores.values()):
        raise ValueError('Invalid local memory candidate binding')
    return scores


def recall(store, owner, query, *, ranker=None):
    if not isinstance(query,str) or not 1 <= len(query.strip()) <= 400:
        raise ValueError('Question must contain 1–400 characters')
    start = time.monotonic()
    docs = store.list(owner)
    if not docs:
        return {'status':'no_memory','items':[], 'external_calls':0}
    try:
        scores = (ranker or local_rank)(query, docs)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {'status':'unavailable','items':[], 'external_calls':0}
    # RRF improves lexical exact matches without treating cosine as confidence.
    dense = sorted(docs,key=lambda d:(-scores[d['id']],d['id']))
    lexical = sorted(docs,key=lambda d:(-lexical_score(query,document_text(d)),d['id']))
    ranks = {d['id']:1/(60+i) for i,d in enumerate(dense,1)}
    for i,d in enumerate(lexical,1):
        if lexical_score(query,document_text(d)) > 0:
            ranks[d['id']] += 1/(60+i)
    candidates = []
    for doc in sorted(docs,key=lambda d:(-ranks[d['id']],d['id'])):
        # Conservative development cutoff; candidates always require explicit selection.
        if scores[doc['id']] < 0.50:
            continue
        try:
            current = store.get(owner, doc['id'])
        except KeyError:
            continue
        if current['revision'] != doc['revision']:
            continue
        candidates.append({**current, 'similarity':round(scores[doc['id']],4),
                           'lexical_overlap':round(lexical_score(query,document_text(doc)),4)})
    return {'status':'candidates' if candidates else 'no_match', 'items':candidates[:3],
            'query':query, 'method':'local_bge_qdrant_rrf', 'external_calls':0,
            'elapsed_ms':round((time.monotonic()-start)*1000,1)}


def local_names():
    if not os.getenv('PHARMACOPOEIA_INDEX'):
        return None
    from tcm.mineru_retrieval import ResearchRetriever
    retriever=ResearchRetriever(os.environ['PHARMACOPOEIA_INDEX'])
    try:
        return tuple(retriever.names)
    finally:
        retriever.connection.close()


def apply_context(store, owner, identifier, revision, query, *, names=None):
    doc=store.get(owner,identifier)
    if doc['revision'] != revision:
        raise ProjectConflict('项目已修改，请重新检索后选择。')
    # Strip historical framing only when the user explicitly marks a new request.
    parts=re.split(r'(?:这次|本次|现在)(?:只)?(?:想|要)?',query)
    current=parts[-1].strip(' ，,。；;：:') if len(parts)>1 else query.strip()
    continuation = bool(re.fullmatch(r'(?:请)?(?:继续|接着|回到)(?:上次|之前|先前)?(?:的)?(?:那个|那组|那两味)?(?:项目|研究|药材|比较|整理)?[吧。！!？?]*',current))
    continuation = continuation or (len(parts)==1 and bool(re.fullmatch(r'(?:请)?(?:继续|接着|回到)(?:上次|之前|先前|那个|那组).{0,60}(?:项目|研究|比较|对比|整理|任务)[吧。！!？?]*',current)) and not re.search(r'并|同时|另外|还要|预测|治疗|用药',current))
    # Pure continuation uses the stored focus. Other requests stay intact, even if unsupported.
    question=doc['question'] if continuation else current
    herbs=doc['herbs']
    if names is None:
        names=local_names()
    if names is not None:
        from tcm.mineru_retrieval import resolve_names
        explicit=resolve_names(current,names)
        if len(explicit)>2:
            raise ProjectConflict('本次明确提到了超过两味药，请缩小研究范围。')
        if explicit:
            herbs=sorted(explicit,key=current.find)
        elif re.search(r'换成|改成|改查|换为|改为|换看',current):
            raise ProjectConflict('未识别本次更换的药材，请在报告表单明确填写。')
    # An unavailable entity catalog must not silently infer a replacement object.
    if names is None and not continuation:
        raise ProjectConflict('药材目录不可用，无法核对本次对象；请在报告表单明确填写。')
    return {'project_id':identifier,'revision':revision,'title':doc['title'],
            'herbs':herbs,'question':question,'original_query':query,
            'question_source':'saved_project' if continuation else 'current_question',
            'object_source':'current_question' if herbs != doc['herbs'] else 'saved_project'}
