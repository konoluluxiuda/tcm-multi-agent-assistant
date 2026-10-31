"""Run the release-bound local demo; corpus stays in read-only host mounts.

No image push or model credentials. Uses host UID so private registered source
files stay mode 0600. Refuses an existing container name rather than replacing it.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

from tcm.rag_benchmark import read


def start(image,release,state,port,name):
    release=release.resolve();state=state.resolve();state.mkdir(parents=True,exist_ok=True,mode=0o700)
    c=read(release/'sources.private.json')['config']
    env={
      'ENABLE_DATAOPS':'1','ENABLE_DATAOPS_RESEARCH':'1','DATAOPS_RESEARCH_DRIVER':'fixed',
      'ENABLE_DATAOPS_AGENT':'0','DATAOPS_DRIVER':'disabled','DATAOPS_ROOT':'/app-memory/dataops',
      'ENABLE_RAG_WORKBENCH':'1','ENABLE_RAG_GENERATION':'0','RAG_RELEASE_PATH':str(release),
      'RAG_RUN_DB':'/app-memory/rag.sqlite3',
      'PHARMACOPOEIA_INDEX':str(release/'index'),'TCM_DB':c['herbs'],'ETCM_ENRICHMENT_DB':c['enrichment'],
      'ETCM_SOURCE_ROOT':c['source_root'],'PHARMACOPOEIA_PDF_HIGH':c['pdf_high'],'PHARMACOPOEIA_PDF_FULL':c['pdf_full'],
      'CHAT_MODEL':'offline-disabled','CHAT_API_KEY':'unused','CHAT_BASE_URL':'http://127.0.0.1:9/v1',
      'EMBEDDING_MODEL':'offline-disabled','EMBEDDING_API_KEY':'unused','EMBEDDING_BASE_URL':'http://127.0.0.1:9/v1','EMBEDDING_DIM':'1024',
      'ENABLE_LANGFUSE':'0','LANGFUSE_TRACING_ENABLED':'false','LANGCHAIN_TRACING_V2':'false','LANGSMITH_TRACING':'false',
      'ENABLE_INTENT_PLANNER':'0','ENABLE_EVIDENCE_ASSISTANT':'0','ENABLE_NATURAL_ROUTING':'1',
      'ENABLE_RESEARCH_TASKS':'0','ENABLE_RESEARCH_PLAN_JOBS':'0','RESEARCH_JOB_DSN':'',
      'CHAT_COOKIE_NAME':'research_selection_container','RESEARCH_TOOLS_ONLY':'1',
      'ENABLE_WEB_SEARCH':'0','ENABLE_PUBMED':'0','ENABLE_IMAGE_ANALYSIS':'0','ENABLE_SPEECH':'0'}
    paths={release,Path(c['herbs']),Path(c['enrichment']),Path(c['source_root']),Path(c['pdf_high']),Path(c['pdf_full'])}
    for p in paths:
        if not p.exists() or p.is_symlink() or ',' in str(p):raise ValueError('Registered read-only mount missing or invalid')
    if any(state==p or state in p.parents or p in state.parents for p in paths):raise ValueError('State and corpus must be separate')
    cmd=['docker','run','--detach','--name',name,'--user',str(os.getuid())+':'+str(os.getgid()),
         '--read-only','--init','--cap-drop=ALL','--security-opt=no-new-privileges',
         '--publish',f'127.0.0.1:{port}:8000','--tmpfs','/tmp:mode=1777',
         '--tmpfs',f'/app/uploads:uid={os.getuid()},gid={os.getgid()},mode=0700',
         '--tmpfs',f'/app/data:uid={os.getuid()},gid={os.getgid()},mode=0700',
         '--mount',f'type=bind,src={state},dst=/app-memory']
    for p in sorted(paths):cmd+=['--mount',f'type=bind,src={p},dst={p},readonly']
    for key,value in env.items():cmd+=['--env',key+'='+value]
    cmd.append(image)
    identifier=subprocess.check_output(cmd,text=True).strip()
    return {'container':name,'id':identifier,'port':port,'image':image,
            'driver':'fixed','external_model_enabled':False,'release_content_id':read(release/'release.json')['content_id'],
            'source_mounts_read_only':True,'state':str(state),'url':f'http://127.0.0.1:{port}/dataops/research'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image',default='tcm-research-chat:user-chat-replay-v1');p.add_argument('--release',type=Path,default=Path('var/data_releases/r1_20261030'))
    p.add_argument('--state',type=Path,default=Path('var/recruitment_delivery_v2/container-state'));p.add_argument('--port',type=int,default=7914)
    p.add_argument('--name',default='tcm-research-r4');args=p.parse_args()
    print(json.dumps(start(args.image,args.release,args.state,args.port,args.name),ensure_ascii=False))
