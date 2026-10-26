"""Synthetic-only process fault fixture; never loaded by the application server."""
import asyncio
import json
import os
from pathlib import Path
from tcm.mcp_server import create_server,serve


async def backend(branch,herb,question):
    marker=Path(os.environ['MCP_TEST_MARKER'])
    if herb=='slow':
        marker.write_text('started')
        try:await asyncio.sleep(100)
        except asyncio.CancelledError:
            marker.write_text('cancelled');raise
    if herb=='broken':raise RuntimeError('private source path must not escape')
    return {'status':'candidates','hits':[{'text':'synthetic','evidence':{'id':'fixture-1'}}]}


if __name__=='__main__':asyncio.run(serve(create_server(backend)))
