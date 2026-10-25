"""Durable audit wrapper; reruns are explicit, never replayed model calls."""
import asyncio
from tcm.research_run_local import run_local_profile


async def local_profile(store, owner, path, disease):
    doc=store.create(owner,disease)
    async def persist(snapshot):
        nonlocal doc
        doc=store.save(owner,doc,snapshot=snapshot)
    try:
        result=await run_local_profile(path,disease,on_state=persist)
        doc=store.save(owner,doc,result=result,status=result['status'])
    except asyncio.CancelledError:
        store.save(owner,doc,status='cancelled')
        raise
    except Exception:
        # No exception text: paths and provider errors are not journal content.
        store.save(owner,doc,status='failed')
        raise
    return {'run_id':doc['id'],'revision':doc['revision'],**result}
