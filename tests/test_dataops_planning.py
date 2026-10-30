import unittest
from langchain_core.messages import AIMessage
from tcm.dataops.planning import STAGES, parse_plan, request_plan, NativePlanDriver


class PlanningTests(unittest.TestCase):
    def reply(self,steps):return AIMessage(content='',tool_calls=[{'id':'plan-1','name':'set_plan','args':{'steps':steps}}])

    def test_native_plan_prerequisites_are_not_optional(self):
        self.assertEqual(parse_plan(self.reply(list(STAGES))),list(STAGES))
        for steps in ([*STAGES[:-1]],list(reversed(STAGES)),[*STAGES,'execute_sql']):
            with self.assertRaises(ValueError):parse_plan(self.reply(steps))

    def test_native_planning_is_one_call_and_requires_authorization(self):
        with self.assertRaises(PermissionError):request_plan(object(),{},'合成任务')
        outer=self
        class Chat:
            max_retries=0;max_tokens=1200;calls=0
            def bind_tools(self,tools,**kw):
                outer.assertFalse(kw['parallel_tool_calls']);return self
            def invoke(self,messages):
                self.calls+=1;outer.assertNotIn('observation',messages[-1]['content'])
                return outer.reply(list(STAGES))
        chat=Chat();steps,_=request_plan(chat,{},'合成任务',authorized=True)
        self.assertEqual(chat.calls,1);self.assertEqual(NativePlanDriver(steps).plan,STAGES)


if __name__=='__main__':unittest.main()
