"""Synthetic session-isolation and concurrent RAG probes against port 7861."""
import concurrent.futures
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

OUT=Path(__file__).with_name('state_stability_results.json')
BASE='http://127.0.0.1:7861'
MARKER='HERB-74291'
records=[]

def request(name,query,session):
    start=time.monotonic()
    record={'name':name,'query':query,'session':session}
    req=urllib.request.Request(BASE+'/chat',data=json.dumps({'query':query}).encode(),
        headers={'Content-Type':'application/json','Cookie':'session_id='+session})
    try:
        try: res=urllib.request.urlopen(req,timeout=150)
        except urllib.error.HTTPError as e: res=e
        body=json.loads(res.read().decode())
        record.update(http_status=res.code,body=body,marker_in_answer=MARKER in str(body.get('response','')))
    except Exception as e:
        record['error']=str(e)
    record['elapsed_s']=round(time.monotonic()-start,2)
    return record

def save(record):
    records.append(record)
    OUT.write_text(json.dumps(records,ensure_ascii=False,indent=2))
    summary=dict(record)
    summary['body_preview']=str(summary.pop('body',{}))[:650]
    print(json.dumps(summary,ensure_ascii=False),flush=True)

save(request('session_A_seed',
 '我正在阅读脑肿瘤医学文献。这次医学学习笔记的编号是 HERB-74291。请记住编号，并只回复已记住；这是虚构的学习笔记，不涉及任何个人信息。',
 'isolation-A'))
question='这次脑肿瘤医学学习笔记的编号是什么？请仅依据本次对话回答；如果我没有告诉你，请明确说不知道。'
save(request('session_A_recall',question,'isolation-A'))
save(request('session_B_isolation',question,'isolation-B'))

query='What general symptoms of brain tumors are described in the medical literature?'
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    futures=[pool.submit(request,'concurrent_rag_'+str(i),query,'concurrency-'+str(i)) for i in (1,2)]
    for f in concurrent.futures.as_completed(futures):
        save(f.result())
print('Completed; inspect logs for retrieval failures masked by successful web fallback.',flush=True)
