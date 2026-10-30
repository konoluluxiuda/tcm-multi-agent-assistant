"""Isolated A/C protocol adapters. Not production runtime or real model evaluation."""
import asyncio
import json
from time import monotonic
from typing import Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import StateGraph, END
from pydantic import Field
from tcm.task_tools import Contract
from tcm.cross_source_report import assemble_report
from tcm.pair_research_report import source_pair, verify_targets, comparison_rows


class Read(Contract):
    herb: str
    branch: Literal['text', 'structured']


class Delegate(Contract):
    herb: str


class Page(Contract):
    page: int = Field(ge=1, le=100)


class Finish(Contract):
    evidence_ids: list[str] = Field(max_length=104)


ARGS = {'read_source': Read, 'delegate': Delegate, 'shared_targets': Page, 'finish': Finish}


class Driver:
    """Fixed native-tool-call SDK substitute; stores inputs for isolation assertions."""
    def __init__(self, actions):
        self.actions = iter(actions)
        self.inputs = []

    def bind_tools(self, schemas):
        self.schemas = schemas
        return self

    async def ainvoke(self, messages):
        self.inputs.append([m.model_dump() for m in messages])
        name, arguments = next(self.actions)
        return AIMessage(content='', tool_calls=[{'name': name, 'args': arguments,
                         'id': str(len(self.inputs)), 'type': 'tool_call'}])


class State(TypedDict, total=False):
    messages: list
    done: bool


class PairProtocol:
    def __init__(self, herbs, question, backend, *, all_targets=True, model_limit=20, tool_limit=12,
                 seconds=60, call_seconds=10):
        if len(herbs) != 2 or len(set(herbs)) != 2:
            raise ValueError('Two distinct host-bound herbs required')
        self.herbs, self.question, self.backend = tuple(herbs), question, backend
        self.all_targets = all_targets
        self.model_limit, self.tool_limit = model_limit, tool_limit
        self.models = self.tools = self.delegations = 0
        if not 0 < seconds <= 180 or not 0 < call_seconds <= 30:
            raise ValueError('Invalid timeout')
        self.seconds, self.call_seconds = seconds, call_seconds
        self.deadline = monotonic() + seconds
        self.sources = {h: {} for h in herbs}
        self.pages, self.evidence, self.trace = [], {}, []
        self.delegated = set()
        self.reason = None

    def reports(self):
        return [assemble_report({k: self.sources[h].get(k, {'status': 'unavailable'})
                                 for k in ('text', 'structured')}, h, self.question)['report']
                for h in self.herbs]

    def complete(self):
        sides = self.reports()
        fields = all(s['status'] == 'complete' and all(any(
            hit['label'] == field and hit.get('evidence') for hit in s['text'].get('hits', []))
            for field in ('功能与主治', '注意')) for s in sides)
        selection, _ = source_pair(sides)
        return bool(fields and selection and self.pages and
                    (not self.all_targets or self.pages[-1]['next_offset'] is None))

    def put(self, key, result):
        self.evidence[key] = result
        return {'evidence_id': key, 'data': result}

    async def query(self, name, args):
        if self.tools >= self.tool_limit:
            raise ValueError('tool_budget')
        self.tools += 1
        self.trace.append({'event': 'tool_dispatch', 'name': name, 'arguments': args})
        return await asyncio.wait_for(self.backend(name, args), self.call_seconds)

    async def action(self, name, raw, role, children):
        args = ARGS[name].model_validate(raw).model_dump()
        if name == 'read_source':
            herb, branch = args['herb'], args['branch']
            if herb not in self.herbs or role not in ('flat', herb) or branch in self.sources[herb]:
                raise ValueError('source_scope')
            result = await self.query(name, args)
            self.sources[herb][branch] = result
            return self.put(f'{herb}:{branch}', result), False
        if name == 'delegate':
            herb = args['herb']
            if role != 'supervisor' or herb not in self.herbs or herb in self.delegated:
                raise ValueError('delegate_scope')
            self.delegated.add(herb)
            self.delegations += 1
            await self.loop(children[herb], herb, children)
            # Parent receives bounded coverage metadata, not a sibling's raw context.
            return {'herb': herb, 'evidence_ids': [f'{herb}:{b}' for b in self.sources[herb]],
                    'available_branches': list(self.sources[herb])}, False
        if name == 'shared_targets':
            if role not in ('flat', 'supervisor'):
                raise ValueError('child_cannot_query_pair')
            selection, reason = source_pair(self.reports())
            if reason:
                raise ValueError(reason)
            page = args['page']
            if page != len(self.pages) + 1 or self.pages and self.pages[-1]['next_offset'] is None:
                raise ValueError('page_sequence')
            result = await self.query(name, {'selection': selection, 'page': page})
            if verify_targets(result, selection).get('status') != 'ok':
                raise ValueError('invalid_evidence')
            offset = (page - 1) * 20
            total = result['total']
            expected = min(20, max(0, total - offset))
            previous = {item['identifier'] for p in self.pages for item in p['items']}
            if (result.get('offset') != offset or result.get('limit') != 20 or
                    result.get('returned_count') != expected or len(result['items']) != expected or
                    result.get('next_offset') != (offset + 20 if offset + 20 < total else None) or
                    self.pages and total != self.pages[0]['total'] or
                    previous.intersection(item['identifier'] for item in result['items'])):
                raise ValueError('invalid_pagination')
            self.pages.append(result)
            return self.put(f'targets:{page}', result), False
        if role in self.herbs:
            expected = {f'{role}:{b}' for b in ('text', 'structured')}
            valid = set(self.sources[role]) == {'text', 'structured'}
        else:
            expected = set(self.evidence)
            valid = self.complete()
        if (not valid or set(args['evidence_ids']) != expected or
                len(args['evidence_ids']) != len(expected)):
            raise ValueError('incomplete_or_invalid_citations')
        return {'status': 'complete'}, True

    async def loop(self, sdk, role, children):
        allowed = (['read_source', 'finish'] if role in self.herbs else
                   ['delegate', 'shared_targets', 'finish'] if role == 'supervisor' else
                   ['read_source', 'shared_targets', 'finish'])
        schemas = [{'type': 'function', 'function': {'name': n, 'description': n,
                    'parameters': ARGS[n].model_json_schema()}} for n in allowed]
        model = sdk.bind_tools(schemas)

        async def step(state):
            if self.models >= self.model_limit or monotonic() >= self.deadline:
                raise ValueError('model_or_time_budget')
            self.models += 1
            self.trace.append({'event': 'model_dispatch', 'role': role})
            reply = await asyncio.wait_for(model.ainvoke(state['messages']), self.call_seconds)
            if len(reply.tool_calls) != 1 or reply.tool_calls[0]['name'] not in allowed:
                raise ValueError('invalid_action')
            call = reply.tool_calls[0]
            result, done = await self.action(call['name'], call['args'], role, children)
            return {'messages': [*state['messages'], reply, ToolMessage(
                content=json.dumps(result, ensure_ascii=False), tool_call_id=call['id'])], 'done': done}

        graph = StateGraph(State)
        graph.add_node('step', step)
        graph.set_entry_point('step')
        graph.add_conditional_edges('step', lambda s: END if s['done'] else 'step')
        task = ({'herb': role, 'fields': ['功能与主治', '注意'], 'structured': True}
                if role in self.herbs else {'herbs': self.herbs, 'question': self.question,
                                           'all_targets': self.all_targets})
        await graph.compile().ainvoke({'messages': [HumanMessage(content=json.dumps(task))], 'done': False},
                                      config={'recursion_limit': 30})

    async def run(self, candidate, sdk, children=None):
        if candidate not in ('A_protocol', 'C_protocol'):
            raise ValueError('Unknown candidate')
        try:
            await asyncio.wait_for(self.loop(sdk, 'flat' if candidate == 'A_protocol' else 'supervisor',
                                            children or {}), self.seconds)
        except (ValueError, KeyError, StopIteration, RuntimeError, asyncio.TimeoutError) as error:
            self.reason = str(error) or type(error).__name__
        return self.result(candidate)

    def result(self, candidate):
        """Common report/acceptance surface for isolated native-runtime adapters."""
        sides = self.reports()
        target = {**self.pages[0], 'items': [i for p in self.pages for i in p['items']],
                  'returned_count': sum(len(p['items']) for p in self.pages),
                  'next_offset': self.pages[-1]['next_offset'], 'pages_read': len(self.pages)} if self.pages else {
            'status': 'unavailable', 'items': []}
        return {'candidate': candidate, 'status': 'complete' if self.reason is None and self.complete() else 'partial',
                'reason': self.reason, 'source_reports': sides, 'targets': target,
                'rows': comparison_rows(sides), 'pages': self.pages, 'trace': self.trace,
                'model_substitute_calls': self.models, 'tool_calls': self.tools,
                'delegations': self.delegations, 'live_model_calls': 0}


def drivers(candidate, herbs=('Alpha', 'Beta'), pages=2):
    def reads(herb):
        return [('read_source', {'herb': herb, 'branch': b}) for b in ('text', 'structured')]
    citations = [f'{h}:{b}' for h in herbs for b in ('text', 'structured')]
    ending = [('shared_targets', {'page': p}) for p in range(1, pages + 1)] + [
        ('finish', {'evidence_ids': citations + [f'targets:{p}' for p in range(1, pages + 1)]})]
    if candidate == 'A_protocol':
        return Driver([a for h in herbs for a in reads(h)] + ending), {}
    children = {h: Driver(reads(h) + [('finish', {'evidence_ids': [f'{h}:text', f'{h}:structured']})]) for h in herbs}
    return Driver([('delegate', {'herb': h}) for h in herbs] + ending), children
