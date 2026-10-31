"""One bounded JSON generation request. No dotenv, retries or implicit calls."""
import json
import os
import hashlib
from urllib.parse import urlsplit

import httpx

from tcm.rag_pipeline import LIMITS, SYSTEM
from tcm.tracing import observation


def request_body(payload,model):
    return {'model':model,'messages':[{'role':'system','content':SYSTEM},
            {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],
            'max_tokens':LIMITS['max_output_tokens'],'temperature':0,'stream':False,
            'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}}


class SingleRequestTransport(httpx.BaseTransport):
    def __init__(self,endpoint,model,stats,inner=None,expected_sha=None):
        self.endpoint,self.model,self.stats=endpoint,model,stats
        self.inner=inner or httpx.HTTPTransport(retries=0)
        self.expected_sha=expected_sha

    def handle_request(self,request):
        body=request.read();data=json.loads(body)
        if (self.stats['requests'] or str(request.url)!=self.endpoint or request.method!='POST' or
                self.expected_sha and hashlib.sha256(body).hexdigest()!=self.expected_sha or
                len(body)>LIMITS['max_request_bytes'] or data.get('model')!=self.model or
                data.get('max_tokens')!=LIMITS['max_output_tokens'] or
                data.get('thinking')!={'type':'disabled'} or data.get('stream') is not False or
                data.get('response_format')!={'type':'json_object'} or 'tools' in data or
                set(data)-{'messages','model','max_tokens','temperature','stream','response_format','thinking'}):
            raise ValueError('Generation request outside budget')
        self.stats.update(requests=1,outcome='unknown',request_bytes=len(body))
        # Mark unknown BEFORE dispatch: callers return originals and never retry.
        response=self.inner.handle_request(request)
        self.stats['outcome']='http_'+str(response.status_code)
        return response

    def close(self):self.inner.close()


class BoundedChat:
    def __init__(self,*,endpoint=None,model=None,key=None,transport_factory=None,expected_sha=None):
        base=os.getenv('RAG_BASE_URL','').rstrip('/')
        self.endpoint=endpoint or (base+'/chat/completions')
        self.model=model or os.getenv('RAG_MODEL','')
        self.key=key or os.getenv('RAG_API_KEY','')
        self.factory=transport_factory
        self.expected_sha=expected_sha
        self.stats={'requests':0,'outcome':'not_dispatched','usage':{}}

    def generate(self,payload):
        url=urlsplit(self.endpoint)
        if not self.model or not self.key or url.scheme!='https' or not url.hostname or url.username or url.password:
            raise ValueError('Generation recipient or credentials missing')
        transport=SingleRequestTransport(self.endpoint,self.model,self.stats,
                                         self.factory() if self.factory else None,self.expected_sha)
        body=request_body(payload,self.model)
        with observation('rag.generate',generation=True,model=self.model,input=payload,
                         metadata={'request_limit':1,'retries':0}) as span:
            try:
                with httpx.Client(transport=transport,timeout=30,follow_redirects=False,trust_env=False) as client:
                    response=client.post(self.endpoint,json=body,headers={'Authorization':'Bearer '+self.key})
                    response.raise_for_status()
                    if len(response.content)>65536:raise ValueError('Provider response too large')
                    result=response.json()
                choices=result.get('choices') or []
                usage=result.get('usage') or {}
                self.stats['usage']={k:usage[k] for k in ('prompt_tokens','completion_tokens','total_tokens')
                                     if type(usage.get(k)) is int and usage[k]>=0}
                if len(choices)!=1 or choices[0].get('finish_reason')!='stop':
                    raise ValueError('Incomplete generation response')
                return {'content':choices[0]['message']['content'],'stats':dict(self.stats)}
            finally:
                known={dst:self.stats['usage'][src] for src,dst in
                       (('prompt_tokens','input'),('completion_tokens','output')) if src in self.stats['usage']}
                span.update(output={'stats':self.stats},usage_details=known or None)
