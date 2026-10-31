from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from langchain_core.messages import AIMessage
from tests.dataops_fixture import make_sources
from tcm.dataops.research_model import ResearchPlan,ResearchFixed
from tcm.dataops.research_graph import ResearchEngine
from tcm.dataops.research_contracts import ResearchGoal
from tcm.dataops.workspace import Workspaces
from tcm.dataops.data_access import canonical
from scripts.eval_dataops_research import score


class PlanTests(unittest.TestCase):
    def test_plan_does_not_recall_model_after_observations_and_receipts_are_restartable(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);records,sources=make_sources(root);store=Workspaces(root/'work');sid=records['Beta']['source_record_id']
            goal=ResearchGoal(query={'names':['Beta'],'limit':20},attributes=['medicinal_part'],document_fields=['功能与主治'],research={'target_count':1})
            with sources() as s:
                pool=s.query(goal.query);witness={'pool':pool,'inspections':{sid:s.inspect(records['Beta'],goal.document_fields)}}
                doc,_=store.reserve_agent('owner','one-plan-request',goal.model_dump(),s.revision,question='按目标生成表格')
            class Model:
                calls=0;usage_metadata={}
                def invoke(self,messages):
                    self.calls+=1
                    return AIMessage(content=canonical({'actions':[
                        {'tool':'inspect_candidates','arguments':{'purpose':'核对字段','source_ids':[sid]}},
                        {'tool':'deliver_result','arguments':{'purpose':'交付','source_ids':[sid],'allow_partial':False}}]}))
            model=Model();result=ResearchEngine(store,'owner',doc['id'],sources,ResearchPlan(model)).run()
            self.assertEqual(model.calls,1);self.assertEqual(result['status'],'complete')
            self.assertEqual(result['result']['research']['provider_requests'],1)
            case={'goal':goal.model_dump(),'expected_status':'complete'}
            self.assertTrue(score(case,result,store,witness)['passed'])
            self.assertEqual(ResearchEngine(store,'owner',doc['id'],sources,ResearchPlan(model)).run()['status'],'complete')
            self.assertEqual(model.calls,1)

    def test_parent_semantics_cannot_be_weakened(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,sources=make_sources(root);store=Workspaces(root/'work')
            goal=ResearchGoal(query={'names':['Beta'],'limit':20},attributes=['medicinal_part'],document_fields=['功能与主治'],research={'target_count':1})
            with sources() as s:d,_=store.reserve_agent('owner','research-parent',goal.model_dump(),s.revision,question='核对')
            parent=ResearchEngine(store,'owner',d['id'],sources,ResearchFixed()).run()
            changed=goal.model_copy(update={'document_fields':['注意']})
            with sources() as s:d,_=store.reserve_agent('owner','research-child',changed.model_dump(),s.revision,question='核对',parent_id=parent['id'],expected_revision=1)
            from tcm.dataops.workspace import Conflict
            with self.assertRaises(Conflict):ResearchEngine(store,'owner',d['id'],sources,ResearchFixed()).run()
