"""Quote membership is checked; traditional paraphrases never become displayed facts."""
import json
import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage
from tcm.evidence_assistant import EvidenceAssistant
from tests.test_evidence_assistant import Model,answer,local


class SupportTests(unittest.TestCase):
    def run_answer(self,value,tool=local):
        model=Model([{'local_queries':['示例药的功效']},value])
        result=EvidenceAssistant(model,local_tool=tool).run('整理示例药资料')
        return result,model

    def test_valid_quote_does_not_authorize_free_pairing_or_classification(self):
        text='属性甲、属性乙。用于场景一、场景二。'
        def source(q):
            r=local(q);r['hits'][0]['text']=text;return r
        draft=answer(quote=text)
        draft['claims'][0]['text']='属性甲对应场景一，因此归入分类丙。'
        draft['claims'].append(dict(draft['claims'][0]))
        draft['limitations']=['分类丙也适用于特殊人群。']
        result,model=self.run_answer(draft,source)
        self.assertEqual(result['status'],'ok')
        self.assertNotIn('属性甲对应场景一',result['message']);self.assertNotIn('分类丙',json.dumps(result['answer'],ensure_ascii=False))
        self.assertEqual(len(result['answer']['claims']),1)
        self.assertIn(text,result['message']);self.assertEqual(model.calls,2)
        self.assertFalse(result['support_check']['semantic_entailment'])

    def test_quote_must_be_literal_and_bound_to_every_cited_source(self):
        drafts=[]
        drafts.append(answer(quote='来源测试文字对应新分类'))
        r=answer();r['claims'][0]['supports'][0]['evidence_id']='L2';drafts.append(r)
        r=answer();r['claims'][0]['supports'][0]['field']='title';drafts.append(r)
        r=answer(quote='   ');drafts.append(r)
        for draft in drafts:
            result,model=self.run_answer(draft)
            self.assertEqual(result['status'],'invalid_support')
            self.assertIsNone(result['answer']);self.assertEqual(model.calls,2)
            self.assertNotIn('来源中的资料记载',result['message'])

    def test_old_unanchored_answer_is_rejected_without_retry(self):
        draft=answer();draft['claims'][0].pop('supports')
        result,model=self.run_answer(draft)
        self.assertEqual(result['status'],'generation_failed');self.assertEqual(model.calls,2)
        self.assertIsNone(result['answer'])

    def test_literature_quote_does_not_claim_semantic_verification(self):
        draft=answer(ids=['P1'],group='literature',quote='Only a title')
        draft['claims'][0]['supports'][0]['field']='title'
        flow=EvidenceAssistant(Model([{'pubmed_query':'fixture'},draft]),
            literature_tool=lambda q:{'status':'ok','articles':[{'pmid':'synthetic:1','title':'Only a title','abstract':[]}]})
        result=flow.run('整理文献')
        self.assertEqual(result['status'],'ok');self.assertFalse(result['semantic_support_verified'])
        self.assertTrue(any('语义支持仍需复核' in x for x in result['answer']['limitations']))

    def test_missing_field_is_named_without_using_generated_limitations(self):
        def tool(query):return {'status':'field_not_observed','hits':[]} if '注意' in query else local(query)
        flow=EvidenceAssistant(Model([{'local_queries':['示例药 功效','示例药 注意']},answer()]),local_tool=tool)
        result=flow.run('整理示例药功效与注意')
        self.assertEqual(result['status'],'partial')
        self.assertTrue(any('示例药 注意' in note and 'field_not_observed' in note for note in result['answer']['limitations']))

    def test_api_reports_invalid_support_as_failed_answer(self):
        import app
        from fastapi.testclient import TestClient
        response={'agent_name':'EVIDENCE_ASSISTANT','messages':[AIMessage(content='摘录未通过检查')],
                  'evidence_answer':{'status':'invalid_support','message':'摘录未通过检查','model_calls':2}}
        with TestClient(app.app) as client,patch.dict('os.environ',{'ENABLE_EVIDENCE_ASSISTANT':'1'}), \
             patch.object(app,'process_query',return_value=response):
            result=client.post('/chat',json={'query':'整理示例药','answer_mode':'evidence'})
            self.assertEqual(result.status_code,502)
            self.assertEqual(result.json()['status'],'unavailable')
