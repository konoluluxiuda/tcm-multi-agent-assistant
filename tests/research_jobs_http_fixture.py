"""Loopback-only acceptance app; not mounted in production."""
import asyncio
from fastapi import FastAPI
from tcm.research_job_api import create_router

class Memory:
    def valid_cookie(self,value):return value in ('acceptance-owner','another-owner')

async def runner(path,question,*,on_state):
    await on_state({'last_node':'decide','model_calls':0,'tool_calls':0,'events':[]})
    await asyncio.sleep(2 if question!='crash' else 60)
    await on_state({'last_node':'execute','model_calls':0,'tool_calls':1,'events':[{'tool':'synthetic_local','status':'ok'}]})
    return {'status':'complete','mode':'synthetic_http_acceptance','run':{'events':[],'evidence':{}}}

app=FastAPI()
app.include_router(create_router(Memory,lambda:'chat',runner=runner))
@app.get('/health')
def health():return {'ok':True}
