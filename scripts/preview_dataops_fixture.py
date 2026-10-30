"""Offline four-record DataOps UI fixture, including a persistent ambiguous candidate."""
import argparse
import os
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--port', type=int, default=7909)
    args = p.parse_args()
    root = args.state.resolve(); root.mkdir(parents=True, exist_ok=True)
    fixture = root / 'fixture'
    if not (fixture / 'herbs.sqlite3').exists():
        from tests.dataops_fixture import make_sources
        make_sources(fixture)
    os.environ.update(ENABLE_DATAOPS='1', ENABLE_DATAOPS_AGENT='1', DATAOPS_DRIVER='synthetic',
        DATAOPS_ROOT=str(root / 'dataops'), RESEARCH_JOB_DB=str(root / 'jobs.sqlite3'),
        RESEARCH_JOB_DSN='', RESEARCH_CHECKPOINT_DSN='', TCM_DATASET_VERSION='fixture-v1',
        TCM_DB=str(fixture / 'herbs.sqlite3'), ETCM_ENRICHMENT_DB=str(fixture / 'enrichment.sqlite3'),
        PHARMACOPOEIA_INDEX=str(fixture / 'index'), LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false')
    from fastapi import FastAPI
    from agents.conversation_store import ConversationStore
    from tcm.dataops.api import create_router
    memory = ConversationStore(root / 'chat.sqlite3')
    app = FastAPI(); app.include_router(create_router(lambda: memory, lambda: 'dataops_fixture_cookie'))
    print(f'合成资料+模型替身：http://127.0.0.1:{args.port}/dataops；Gamma有两个候选，不含真实药典资料。', flush=True)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__': main()
