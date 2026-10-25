"""Exercise the installed SDK with synthetic HTTP responses; no network or dotenv."""
import asyncio
import json
import os

# This CLI is always offline, including optional SDK tracing.
for key in ('LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2', 'LANGSMITH_TRACING'):
    os.environ[key]='false'
from tcm.research_run_protocol import evaluate


def main():
    result=asyncio.run(evaluate())
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if result['run']['status']!='complete':raise SystemExit(1)


if __name__=='__main__':main()
