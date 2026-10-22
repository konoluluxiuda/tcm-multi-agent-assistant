"""Relationship focus survives restart, but never crosses owners, identities or snapshots."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_herb_target_paths as fixtures
from tcm.relation_memory import execute_turn,context_from
from tcm.relation_queries import parse
from tcm.agent_research import pack_contexts
from agents.conversation_store import ConversationStore,SessionExpired


class RelationMemoryTests(unittest.TestCase):
    def setUp(self):
        fixtures.HerbTargetPathTests.setUp(self)

    def selected(self):
        result,focus=execute_turn(parse('TestHerb有哪些成分'),None)
        self.assertEqual(result['status'],'ok');self.assertIsNotNone(focus)
        return focus

    def test_small_binding_survives_storage_reload_and_can_follow_to_paths(self):
        focus=self.selected();encoded=json.dumps(focus)
        self.assertLess(len(encoded),1500)
        self.assertNotIn('Compound',encoded);self.assertNotIn('raw_row',encoded)
        path=self.root/'checkpoint.sqlite3';store=ConversationStore(path)
        store.save_serialized('session',[],pack_contexts(None,None,None,focus),revision=0)
        restored=context_from(ConversationStore(path).load_serialized('session').tcm_context)
        result,new_focus=execute_turn(parse('这些成分对应哪些靶点'),restored)
        self.assertEqual(result['relations']['data']['total'],26)
        self.assertEqual(new_focus['query_kind'],'herb_target_paths')
        self.assertIn('全部成分',''.join(result['notes']))

    def test_missing_focus_or_target_focus_never_supplies_an_implicit_herb(self):
        result,focus=execute_turn(parse('这些成分对应哪些靶点'),None)
        self.assertEqual(result['status'],'clarification_needed');self.assertIsNone(focus)
        _,target=execute_turn(parse('/relations 靶点成分 P12345',explicit=True),None)
        result,focus=execute_turn(parse('这些成分对应哪些靶点'),target)
        self.assertEqual(result['status'],'clarification_needed');self.assertIsNone(focus)
        self.assertNotIn('relations',result)

    def test_source_sha_or_dataset_fingerprint_change_invalidates_focus(self):
        focus=self.selected();focus['herb_sources'][0]['file_sha256']='different'
        result,new=execute_turn(parse('这些成分对应哪些靶点'),focus)
        self.assertIsNone(new);self.assertIn('已改变',result['message'])
        focus=self.selected()
        with sqlite3.connect(self.enrichment) as con:con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='source_fingerprint'")
        result,new=execute_turn(parse('这些成分对应哪些靶点'),focus)
        self.assertEqual(result['status'],'clarification_needed');self.assertIsNone(new)

    def test_target_export_change_invalidates_path_replay(self):
        _,focus=execute_turn(parse('TestHerb的成分关联哪些靶点'),None)
        with sqlite3.connect(self.targets) as con:con.execute("UPDATE metadata SET value='\"changed\"' WHERE key='export_sha256'")
        result,new=execute_turn({'followup':True,'action':'replay'},focus)
        self.assertEqual(result['status'],'clarification_needed');self.assertIsNone(new)

    def test_expiry_removes_relation_payload_too(self):
        now=[100];store=ConversationStore(self.root/'expiry.sqlite3',ttl_seconds=1,clock=lambda:now[0])
        store.save_serialized('session',[],pack_contexts(None,None,None,self.selected()),revision=0)
        now[0]=102
        with self.assertRaises(SessionExpired):store.load_serialized('session')
        with sqlite3.connect(store.path) as con:self.assertEqual(con.execute('SELECT payload FROM conversations').fetchone()[0],'{}')

    def test_rephrased_intent_is_bounded_and_does_not_claim_general_semantics(self):
        request=parse('帮我把TestHerb的成分和相关靶点串起来')
        self.assertEqual(request['kind'],'herb_target_paths')
        self.assertIsNone(parse('帮我把TestHerb的成分和相关靶点串起来并判断能否替代降压药'))

    def test_api_restart_refresh_isolation_clear_and_topic_switch(self):
        import app
        import agents.agent_decision as ad
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        def forbidden(*a,**kw):raise AssertionError('Relationship memory called a model')
        memory=ConversationStore(self.root/'api-memory.sqlite3')
        with patch.object(ad,'memory',memory),patch.object(ad.config.agent_decision,'llm',RunnableLambda(forbidden)), \
             patch.object(ad.config.conversation,'llm',RunnableLambda(forbidden)),patch.object(ad.LocalGuardrails,'check_input',forbidden), \
             patch.object(ad.LocalGuardrails,'check_output',forbidden),TestClient(app.app) as client,redirect_stdout(io.StringIO()):
            result=client.post('/chat',json={'query':'TestHerb有哪些成分'});self.assertEqual(result.status_code,200)
            cookie=client.cookies[app.SESSION_COOKIE_NAME]
            snapshot=memory.load_serialized(cookie);self.assertIsNotNone(context_from(snapshot.tcm_context))
            self.assertNotIn('Compound',json.dumps(snapshot.tcm_context)+json.dumps(snapshot.messages))
            with patch.object(ad,'memory',ConversationStore(memory.path)):
                restored=client.get('/conversation').json()['assistant']
                self.assertEqual(restored['relations']['data']['total'],4)
                response=client.post('/chat',json={'query':'这些成分对应哪些靶点'})
                self.assertEqual(response.json()['assistant']['relations']['data']['total'],26)
            with TestClient(app.app) as other:
                self.assertEqual(other.post('/chat',json={'query':'这些成分对应哪些靶点'}).json()['assistant']['status'],'clarification_needed')
            client.post('/chat',json={'query':'/herb TestHerb'})
            self.assertIsNone(context_from(memory.load_serialized(cookie).tcm_context))
            self.assertEqual(client.post('/chat',json={'query':'这些成分对应哪些靶点'}).json()['assistant']['status'],'clarification_needed')
            client.post('/chat',json={'query':'TestHerb有哪些成分'});client.delete('/conversation')
            self.assertEqual(client.post('/chat',json={'query':'这些成分对应哪些靶点'}).json()['assistant']['status'],'clarification_needed')


if __name__=='__main__':unittest.main()
