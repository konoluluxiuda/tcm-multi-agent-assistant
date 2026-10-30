"""Native Deep Agents adapter over the A/C host ledger; optional isolated dependency."""
import asyncio
import json
from time import monotonic

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from experiments.architecture_candidates.pair_protocol import ARGS


BUSINESS_TOOLS = ('read_source', 'shared_targets', 'finish')


async def run_deepagents_pair(protocol, sdk):
    """Allowlisted model gateway + actual native graph; no persistence claim here."""
    from deepagents import create_deep_agent
    offered, dispatched = [], []
    finished = False
    bound = None

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return 'isolated-pair-runtime-gateway'

        def bind_tools(self, tools, **kwargs):
            nonlocal bound
            schemas = [convert_to_openai_tool(tool) for tool in tools]
            names = [schema['function']['name'] for schema in schemas]
            offered.append(names)
            # Keep the business scope identical; default harness tools stay unreachable.
            schemas = [schema for schema in schemas if schema['function']['name'] in BUSINESS_TOOLS]
            if set(s['function']['name'] for s in schemas) != set(BUSINESS_TOOLS):
                raise ValueError('business_schema_missing')
            bound = sdk.bind_tools(schemas)
            return self

        def _generate(self, *args, **kwargs):
            raise RuntimeError('Only async calls are supported by this adapter')

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            if protocol.reason:
                raise ValueError(protocol.reason)
            if protocol.models >= protocol.model_limit or monotonic() >= protocol.deadline:
                raise ValueError('model_or_time_budget')
            protocol.models += 1
            protocol.trace.append({'event': 'model_dispatch', 'role': 'deepagents'})
            if bound is None:
                raise ValueError('unbound_model')
            reply = await asyncio.wait_for(bound.ainvoke(messages), protocol.call_seconds)
            # Prevent native tool-validation recovery from silently introducing retries.
            protocol.admit_batch(reply.tool_calls, 'flat')
            dispatched.extend(call['name'] for call in reply.tool_calls)
            return ChatResult(generations=[ChatGeneration(message=reply)])

    def tool(name):
        async def invoke(**arguments):
            nonlocal finished
            try:
                result, done = await protocol.action(name, arguments, 'flat', {})
                if done:
                    finished = True
                return json.dumps(result, ensure_ascii=False)
            except Exception as error:
                protocol.reason = str(error) or type(error).__name__
                raise
        return StructuredTool.from_function(coroutine=invoke, name=name, description=name,
            args_schema=ARGS[name], return_direct=name == 'finish')

    try:
        # create_deep_agent and its native middleware execute the business tool loop.
        agent = create_deep_agent(model=Model(), tools=[tool(n) for n in BUSINESS_TOOLS],
            system_prompt='Use the host-bound pair task and business tools. Finish with evidence IDs.',
            subagents=[])
        await asyncio.wait_for(agent.ainvoke({'messages': [HumanMessage(content=json.dumps({
            'herbs': protocol.herbs, 'question': protocol.question, 'all_targets': protocol.all_targets}))]},
            config={'recursion_limit': 80}), protocol.seconds)
        if not finished:
            protocol.reason = 'explicit_finish_missing'
    except Exception as error:
        protocol.reason = protocol.reason or str(error) or type(error).__name__
    result = protocol.result('B_deepagents')
    result['native_tools_offered'] = sorted({name for names in offered for name in names})
    result['model_tools_exposed'] = list(BUSINESS_TOOLS)
    result['native_business_dispatches'] = dispatched
    result['policy_adapter'] = ['model_tool_allowlist', 'bounded_batch_and_argument_validation',
                                'host_evidence_ledger', 'shared_budget', 'finish_return_direct']
    result['checkpoint_recovery_tested'] = False
    return result
