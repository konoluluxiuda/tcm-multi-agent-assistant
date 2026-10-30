"""Synthetic report branches on the real preview server; acceptance only."""
import asyncio
import json
import os
from pathlib import Path
import tcm.report_checkpoint as checkpoint

original=checkpoint.run_checkpoint_report


async def synthetic_report(owner,doc,*,on_state=None):
    directory=Path(os.environ['POSTGRES_FIXTURE_DIR'])
    async def backend(branch,herb,question):
        with (directory/'calls.jsonl').open('a') as f:f.write(json.dumps({'branch':herb+'_'+branch if doc['payload']['kind']=='pair_report' else branch})+'\n')
        if branch=='structured' and (doc['payload']['kind']!='pair_report' or herb=='b'):
            while not (directory/'release').exists():await asyncio.sleep(.1)
            if doc['payload']['kind']!='pair_report':return {'status':'ok','rows':[{'label':'合成属性','value':'仅验收','source':{'json_pointer':'/fixture'}}]}
        if doc['payload']['kind']=='pair_report':
            from tests.test_pair_research_report import report
            return report(herb)[branch]
        return {'status':'candidates','hits':[{'name':'示例叶','label':'功能与主治','text':'合成恢复验收文本','chunk_id':'fixture-text','evidence':[]}]}
    async def target(branch,herb,question):
        from tests.test_pair_research_report import targets
        with (directory/'calls.jsonl').open('a') as f:f.write(json.dumps({'branch':'target'})+'\n')
        return targets(json.loads(question))
    return await original(owner,doc,on_state=on_state,backend=backend,target_backend=target,fingerprint='http-synthetic-v1')


if __name__=='__main__':
    checkpoint.run_checkpoint_report=synthetic_report
    from scripts.preview_local_tasks import main
    main()
