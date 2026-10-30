"""Synthetic catalogs behind real HTTP queue/checkpoint UI; not a deployment entrypoint."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

import tcm.report_checkpoint as checkpoint
from tcm.cross_source_report import text_search, structured_search
from tcm.relation_queries import shared_targets

original = checkpoint.run_checkpoint_report


async def synthetic_report(owner, doc, *, on_state=None):
    directory = Path(os.environ['PAIR_PAGINATION_FIXTURE_DIR'])
    root = directory / 'data'
    env = {'TCM_DB': str(root/'herbs.sqlite3'), 'TCM_DATASET_VERSION': 'v1',
           'ETCM_ENRICHMENT_DB': str(root/'derived/enrichment.sqlite3'),
           'PHARMACOPOEIA_INDEX': str(root/'text-index'),
           'LOCAL_DENSE_MODEL': '', 'LOCAL_DENSE_CACHE': '', 'LOCAL_RERANKER_MODEL': ''}
    def record(branch):
        with (directory/'calls.jsonl').open('a') as f:
            f.write(json.dumps({'branch': branch})+'\n')
    async def backend(branch, herb, question):
        record(herb+'_'+branch)
        with patch.dict(os.environ, env):
            return text_search(herb, question) if branch == 'text' else structured_search(herb)
    async def target(branch, herb, question):
        selection = json.loads(question)
        offset = selection.get('offset', 0)
        record('target_'+str(offset//20+1))
        if offset == 20:
            if (directory/'fail_second').exists():
                raise asyncio.TimeoutError()
            while not (directory/'release').exists():
                await asyncio.sleep(.1)
        result = shared_targets(env['ETCM_ENRICHMENT_DB'], selection['records'], selection['version'], offset=offset)
        return {**result, 'status': 'ok', 'source_ids': [r['source_record_id'] for r in selection['records']],
                'dataset_version': selection['version']}
    files = [root/'herbs.sqlite3', root/'derived/enrichment.sqlite3', root/'text-index/manifest.json',
             Path(__file__), Path(checkpoint.__file__).with_name('pair_research_report.py')]
    fingerprint = hashlib.sha256(b''.join(hashlib.sha256(p.read_bytes()).digest() for p in files)).hexdigest()
    return await original(owner, doc, on_state=on_state, backend=backend, target_backend=target,
                          fingerprint=fingerprint)


if __name__ == '__main__':
    directory = Path(os.environ['PAIR_PAGINATION_FIXTURE_DIR'])
    if not (directory/'data').exists():
        from scripts.check_pair_protocol import synthetic_fixture
        with synthetic_fixture() as fixture:
            shutil.copytree(fixture.root, directory/'data')
    checkpoint.run_checkpoint_report = synthetic_report
    from scripts.preview_local_tasks import main
    main()
