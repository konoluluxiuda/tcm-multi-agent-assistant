import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from langchain_openai import ChatOpenAI
from tests.rag_release_fixture import fixture
from tcm.data_release import build
from tcm.dataops.research_model import ResearchFixed
from scripts import eval_dataops_research as evaluation


class LiveProtocolTests(unittest.TestCase):
    def test_full_six_runs_use_immutable_request_response_receipts_and_cannot_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);c=fixture(root/'source')
            with sqlite3.connect(c['enrichment']) as db:
                db.execute('ALTER TABLE name_candidates ADD COLUMN evidence TEXT')
                for sid,eid,name in db.execute('SELECT herb_id,entry_id,name_zh FROM name_candidates').fetchall():
                    db.execute('UPDATE name_candidates SET evidence=? WHERE herb_id=?',(json.dumps({'entry_id':eid,'name_zh':name}),sid))
            release=root/'release';build(c,release)
            common={'query':{'names':['Alpha','Beta'],'limit':20},'attributes':['medicinal_part'],
                    'document_fields':['贮藏'],'research':{'target_count':2,'monograph_only':False}}
            cases=[{'id':'S01','question':'核对两条贮藏并交付','goal':common,'expected_status':'complete'},
                   {'id':'S02','question':'旧表补上注意，缺失不补造','goal':{**common,'document_fields':['贮藏','注意']},
                    'parent_fields':['贮藏'],'expected_status':'partial'}]
            suite=root/'suite.json';suite.write_text(json.dumps({'reference_status':'fixture_only','cases':cases}))
            requests=[]
            def respond(req):
                body=json.loads(req.content);requests.append(body)
                raw=json.loads(next(m['content'] for m in reversed(body['messages']) if m['role']=='user'))
                if body.get('response_format'):
                    ctx=raw['context'];ids=ctx['parent_records'] or [c['source_id'] for c in ctx['candidates']][:2]
                    content=json.dumps({'actions':[
                        {'tool':'inspect_candidates','arguments':{'purpose':'核对字段','source_ids':ids}},
                        {'tool':'deliver_result','arguments':{'purpose':'保留缺失交付','source_ids':ids,'allow_partial':True}}]})
                    message={'role':'assistant','content':content}
                else:
                    tools={t['function']['name'] for t in body['tools']}
                    self.assertEqual('list_candidates' in tools,not raw['listed'])
                    for i,m in enumerate(body['messages']):
                        if m['role']=='tool':
                            previous=body['messages'][i-1]
                            self.assertEqual(previous['role'],'assistant')
                            self.assertEqual(m['tool_call_id'],previous['tool_calls'][0]['id'])
                    action=ResearchFixed().decide(raw)
                    message={'role':'assistant','content':None,'tool_calls':[{'id':'call-'+str(len(requests)),
                        'type':'function','function':{'name':action['tool'],'arguments':json.dumps(action['arguments'])}}]}
                return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':1,'model':'fixture',
                    'choices':[{'index':0,'finish_reason':'tool_calls' if 'tool_calls' in message else 'stop','message':message}],
                    'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
            def model(**kwargs):
                return ChatOpenAI(model='fixture',api_key='test-only',base_url='https://api.deepseek.com',
                    max_tokens=1200,max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(respond)))
            out=root/'evaluation'
            with patch.object(evaluation,'SUITE',suite),patch.object(evaluation,'configured_model',model),\
                 patch('dotenv.dotenv_values',return_value={'CHAT_MODEL':'fixture','CHAT_API_KEY':'test-only','CHAT_BASE_URL':'https://api.deepseek.com'}):
                evaluation.prepare(release,out);report=evaluation.execute(release,out)
                self.assertEqual(len(report['records']),6);self.assertTrue(all(r['passed'] for r in report['records']))
                self.assertEqual(report['requests_total'],len(requests));self.assertLessEqual(len(requests),22)
                for case in ('S01_P','S01_A','S02_P','S02_A'):
                    request_paths=list((out/case).glob('request_*.private.json'))
                    response_paths=list((out/case).glob('response_*.private.json'))
                    self.assertEqual(len(request_paths),len(response_paths));self.assertTrue(request_paths)
                previous=len(requests)
                with self.assertRaises(FileExistsError):evaluation.execute(release,out)
                self.assertEqual(len(requests),previous)
