"""Local stdio MCP server; tools only, no model callbacks or network listener."""
import asyncio
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from tcm.mcp_contract import specifications, invoke


def create_server(backend=None):
    server=Server('tcm-readonly-research',version='1.0.0')

    @server.list_tools()
    async def list_tools():
        return [types.Tool(**s,annotations=types.ToolAnnotations(readOnlyHint=True,destructiveHint=False,
                       idempotentHint=True,openWorldHint=False)) for s in specifications()]

    @server.call_tool(validate_input=False)
    async def call_tool(name,arguments):
        # Shared Pydantic validation produces the same stable errors as direct calls.
        result=await invoke(name,arguments,backend=backend)
        return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],
                                    structuredContent=result,isError=result['status']=='error')
    return server


async def serve(server=None):
    server=server or create_server()
    async with stdio_server() as (read,write):
        await server.run(read,write,server.create_initialization_options())


if __name__=='__main__':asyncio.run(serve())
