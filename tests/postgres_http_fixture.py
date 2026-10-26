"""Synthetic report branches on the real preview server; acceptance only."""
import asyncio
import json
import os
from pathlib import Path
import tcm.report_checkpoint as checkpoint

original=checkpoint.run_checkpoint_report


async def synthetic_report(owner,doc,*,on_state=None):
    directory=Path(os.environ['POSTGRES_FIXTURE_DIR'])
    async def backend(branch,*unused):
        with (directory/'calls.jsonl').open('a') as f:f.write(json.dumps({'branch':branch})+'\n')
        if branch=='structured':
            while not (directory/'release').exists():await asyncio.sleep(.1)
            return {'status':'ok','rows':[{'label':'合成属性','value':'仅验收','source':{'json_pointer':'/fixture'}}]}
        return {'status':'candidates','hits':[{'name':'示例叶','label':'功能与主治','text':'合成恢复验收文本','chunk_id':'fixture-text','evidence':[]}]}
    return await original(owner,doc,on_state=on_state,backend=backend,fingerprint='http-synthetic-v1')


if __name__=='__main__':
    checkpoint.run_checkpoint_report=synthetic_report
    from scripts.preview_local_tasks import main
    main()
