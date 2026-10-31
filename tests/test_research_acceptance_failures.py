"""Replay observed failure classes with local fixtures, never real model calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from tests.dataops_fixture import make_sources
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_model import ResearchFixed
from tcm.dataops.workspace import Workspaces
from tcm.dataops.artifacts import verify


class FailureTests(unittest.TestCase):
    def test_over_delivery_is_stopped_and_explicit_new_fixed_task_can_deliver_empty_result(self):
        # Reduced replay of N03's bad delivery, not a claim to replay model reasoning.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);records,sources=make_sources(root);store=Workspaces(root/'work')
            names=['Alpha','Beta','Delta'];ids=sorted(records[n]['source_record_id'] for n in names)
            goal=ResearchGoal(query={'names':names,'limit':20},attributes=['medicinal_part'],
                              document_fields=['注意'],research={'target_count':2}).model_dump()
            with sources() as s:
                doc,_=store.reserve_agent('owner','native-failure',goal,s.revision,question='缺项不算齐全')
            class Script:
                label='acceptance_failure_fixture';max_response_corrections=0
                def decide(self,context):
                    if not context['listed']:tool,args='list_candidates',{}
                    elif context['unread']:tool,args='inspect_candidates',{'source_ids':ids}
                    else:tool,args='deliver_result',{'source_ids':ids,'allow_partial':True}
                    return {'tool':tool,'arguments':{'purpose':'复现不合格记录误作交付的参数错误',**args}}
            failed=ResearchEngine(store,'owner',doc['id'],sources,Script()).run()
            self.assertEqual(failed['status'],'partial');self.assertIsNone(failed.get('artifact'))
            self.assertEqual(failed['error'],'no_progress')
            self.assertTrue(any(e.get('reason')=='too_many_delivery_records' for e in failed['progress']['events']))
            before=deepcopy(store.get('owner',doc['id']))
            # This is an explicit separate baseline, never an automatic retry of failed model dispatch.
            with sources() as s:
                fixed,_=store.reserve_agent('owner','explicit-fixed-fallback',goal,s.revision,question='同目标固定核对')
            result=ResearchEngine(store,'owner',fixed['id'],sources,ResearchFixed()).run()
            self.assertEqual(result['status'],'partial');self.assertEqual(result['result']['rows'],[])
            self.assertEqual(result['result']['research']['provider_requests'],0)
            verify(store.root/fixed['id']/result['artifact']['filename'],result['result'])
            self.assertEqual(store.get('owner',doc['id']),before)


if __name__=='__main__':unittest.main()
