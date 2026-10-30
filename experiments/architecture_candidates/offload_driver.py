"""Fixed native-tool-call SDK substitute; no model provider."""
import json
from langchain_core.messages import AIMessage,ToolMessage

class Driver:
    def __init__(self,offset=7000,attack=None):self.offset=offset;self.attack=attack
    def bind_tools(self,*a,**kw):return self
    async def ainvoke(self,messages):
        obs=[json.loads(m.content) for m in messages if isinstance(m,ToolMessage)]
        if not obs:name,args='fetch_profile',{}
        elif len(obs)==1:
            name,args='read_evidence',{'evidence_id':'obs-1','evidence_handle':obs[0].get('evidence_handle'),
                                     'field':'target_ids','offset':self.offset,'limit':1}
            if self.attack=='handle':args['evidence_handle']='0'*32
            if self.attack=='offset':args['offset']+=1
            if self.attack=='early_finish':name,args='finish_research',{'evidence_ids':['obs-1']}
        else:
            name,args='finish_research',{'evidence_ids':['fake' if self.attack=='citation' else 'obs-1']}
        return AIMessage(content='',tool_calls=[{'id':f'call-{len(obs)}','name':name,'args':args,'type':'tool_call'}])
