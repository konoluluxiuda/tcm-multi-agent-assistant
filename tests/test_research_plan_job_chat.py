"""Actual chat turn persists a queued-plan focus, not an in-request tool execution."""
from contextlib import redirect_stdout
import asyncio
import io
import json
import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from tests import test_research_plan_chat as base
from tests.research_report_fixture import ReportFixture,QUESTION


class PlanJobChatTests(unittest.TestCase):
    def setUp(self):
        helper=base.ChatTests()
        with patch.object(base,'BusinessFixture',ReportFixture):helper.setUp()
        self.addCleanup(helper.doCleanups);self.helper=helper
        env=patch.dict(os.environ,{'ENABLE_RESEARCH_PLAN_JOBS':'1','RESEARCH_JOB_DB':str(helper.f.root/'jobs.sqlite3'),
            'RESEARCH_JOB_DSN':'','RESEARCH_CHECKPOINT_DSN':''})
        env.start();self.addCleanup(env.stop)
        self.client=TestClient(helper.app.app).__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def turn(self,q,key='chat-plan-one'):
        with redirect_stdout(io.StringIO()):return self.client.post('/chat',json={'query':q,'request_key':key})

    def test_chat_returns_queued_focus_and_refreshes_same_terminal_job(self):
        with patch('tcm.intent_planner.plan_question',side_effect=AssertionError('Unexpected provider')):
            first=self.turn(QUESTION).json()['assistant']
        self.assertIn(first['status'],('queued','running'));identifier=first['run_id']
        self.client.get('/api/research/jobs/'+identifier+'/events')
        result=self.client.get('/conversation').json()['assistant']
        self.assertEqual(result['status'],'ok',result);self.assertEqual(result['run_id'],identifier)
        self.assertEqual(result['execution']['tool_calls'],6)
        duplicate=self.turn(QUESTION).json()['assistant'];self.assertEqual(duplicate['run_id'],identifier)
        self.assertEqual(len(self.client.get('/api/research/jobs').json()['items']),1)
        saved=self.helper.memory.load(self.client.cookies[self.helper.app.SESSION_COOKIE_NAME])
        self.assertNotIn('P00000',json.dumps(saved.tcm_context))
        self.assertNotIn('SYNTHETIC_Alpha',json.dumps(saved.tcm_context))
        self.assertEqual(self.turn('出处呢',key='chat-replay-one').json()['assistant']['run_id'],identifier)

    def test_cancel_from_chat_and_restored_status(self):
        async def blocked(*args,**kwargs):await asyncio.Future()
        with patch('tcm.cross_source_report.local_branch',side_effect=blocked):
            first=self.turn(QUESTION).json()['assistant'];identifier=first['run_id']
            busy=self.turn(QUESTION,key='second-active-job').json()['assistant']
            self.assertEqual(busy['status'],'busy')
            self.assertEqual(self.client.get('/conversation').json()['assistant']['job_id'],identifier)
            cancelled=self.client.post('/api/research/jobs/'+identifier+'/cancel');self.assertEqual(cancelled.status_code,200)
            self.client.get('/api/research/jobs/'+identifier+'/events')
        restored=self.client.get('/conversation').json()['assistant']
        self.assertEqual(restored['status'],'cancelled',restored)
        self.assertEqual(restored['job_id'],identifier)
