"""Bounded read batches must retain complete tool messages, budgets and source scope."""
import unittest
from langchain_core.messages import AIMessage, ToolMessage
from experiments.architecture_candidates.pair_protocol import PairProtocol, Driver, drivers
from experiments.architecture_candidates.deepagents_pair import run_deepagents_pair
from scripts.check_pair_protocol import synthetic_fixture, backend_for, verify_raw_witnesses


class BatchDriver(Driver):
    async def ainvoke(self, messages):
        self.inputs.append([m.model_dump() for m in messages])
        batch = next(self.actions)
        return AIMessage(content='', tool_calls=[{'name':name, 'args':args,
            'id':f'{len(self.inputs)}-{i}', 'type':'tool_call'} for i,(name,args) in enumerate(batch)])


class BatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = synthetic_fixture()
        self.fixture = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def protocol(self, **kwargs):
        return PairProtocol(('Alpha','Beta'),'功能与主治、注意事项和全部共同靶点',backend_for(self.fixture),**kwargs)

    async def test_all_candidates_finish_valid_batches_with_same_evidence(self):
        for candidate in ('A_protocol','B_deepagents','C_protocol'):
            plain, children = drivers('A_protocol' if candidate=='B_deepagents' else candidate)
            actions = list(plain.actions)
            count = 2 if candidate=='C_protocol' else 4
            sdk = BatchDriver([actions[:count], *[[a] for a in actions[count:]]])
            if children:
                for herb, child in children.items():
                    actions = list(child.actions)
                    children[herb] = BatchDriver([actions[:2], actions[2:]])
            protocol = self.protocol()
            result = (await run_deepagents_pair(protocol,sdk) if candidate=='B_deepagents' else
                      await protocol.run(candidate,sdk,children))
            self.assertEqual(result['status'],'complete')
            self.assertEqual(result['tool_calls'],6)
            self.assertEqual(len(result['targets']['items']),23)
            self.assertEqual(verify_raw_witnesses(self.fixture.root,result),48)
            self.assertEqual(result['model_substitute_calls'],8 if candidate=='C_protocol' else 4)
            # Each response's tool-call IDs have matching ToolMessages in the next input.
            for model in [sdk,*children.values()]:
                for messages in model.inputs[1:]:
                    calls = {c['id'] for m in messages for c in m.get('tool_calls',[])}
                    observations = {m['tool_call_id'] for m in messages if m.get('type')=='tool'}
                    self.assertEqual(calls,observations)

    async def test_bad_second_action_prevents_entire_batch_dispatch(self):
        for candidate in ('A_protocol','B_deepagents'):
            sdk = BatchDriver([[('read_source',{'herb':'Alpha','branch':'text'}),
                                ('read_source',{'herb':'Other','branch':'text'})]])
            p = self.protocol()
            result = await run_deepagents_pair(p,sdk) if candidate=='B_deepagents' else await p.run(candidate,sdk)
            self.assertEqual(result['status'],'partial')
            self.assertEqual(result['tool_calls'],0)

    async def test_duplicate_reads_mixed_finish_and_dependent_pages_rejected(self):
        batches = [
            [('read_source',{'herb':'Alpha','branch':'text'})]*2,
            [('shared_targets',{'page':1}),('shared_targets',{'page':2})],
            [('read_source',{'herb':'Alpha','branch':'text'}),('finish',{'evidence_ids':[]})]]
        for batch in batches:
            p = self.protocol()
            result = await p.run('A_protocol',BatchDriver([batch]))
            self.assertEqual(result['status'],'partial')
            self.assertEqual(result['tool_calls'],0)

    async def test_known_batch_budget_checked_before_dispatch(self):
        p = self.protocol(tool_limit=1)
        batch = [('read_source',{'herb':'Alpha','branch':b}) for b in ('text','structured')]
        result = await p.run('A_protocol',BatchDriver([batch]))
        self.assertEqual(result['reason'],'tool_budget')
        self.assertEqual(result['tool_calls'],0)

    def test_duplicate_call_ids_and_child_scope_rejected(self):
        p = self.protocol()
        calls = [{'id':'same','name':'read_source','args':{'herb':'Alpha','branch':b}}
                 for b in ('text','structured')]
        with self.assertRaisesRegex(ValueError,'invalid_call_ids'):
            p.admit_batch(calls,'flat')
        calls[1]['id']='different'
        calls[1]['args']['herb']='Beta'
        with self.assertRaisesRegex(ValueError,'batch_source_scope'):
            p.admit_batch(calls,'Alpha')


if __name__=='__main__':
    unittest.main()
