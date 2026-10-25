"""Two independent research subgraphs followed by source-bound target comparison."""
import asyncio
import json
import unicodedata
from typing import TypedDict

from tcm.cross_source_report import run_report, local_branch


def validate_pair(herbs):
    if not isinstance(herbs, list) or len(herbs) != 2:
        raise ValueError('Exactly two herbs are required')
    if any(not isinstance(x, str) or not 1 <= len(x.strip()) <= 80 for x in herbs):
        raise ValueError('Invalid herb name')
    names = [x.strip() for x in herbs]
    if len({unicodedata.normalize('NFKC', x).casefold() for x in names}) != 2:
        raise ValueError('Choose two different herbs')
    return names


def comparison_rows(reports):
    """Align labels only; never identify entities or infer semantic contradictions."""
    table = {}
    for side, report in enumerate(reports):
        for kind, values in [('药典', report.get('text', {}).get('hits', [])),
                             ('ETCM', report.get('structured', {}).get('rows', []))]:
            for item in values:
                key = (kind, item['label'])
                row = table.setdefault(key, {'source_type': kind, 'field': item['label'], 'left': [], 'right': []})
                cell = {'value': item['text'] if kind == '药典' else item['value'], 'evidence': item}
                row['left' if side == 0 else 'right'].append(cell)
    rows = []
    for key in sorted(table):
        row = table[key]
        left, right = row['left'], row['right']
        row['comparison'] = ('missing' if not left or not right else
                             'multiple_values' if len(left) != 1 or len(right) != 1 else
                             'same_display_value' if left[0]['value'] == right[0]['value'] else 'different_display_value')
        rows.append(row)
    return rows


def source_pair(reports):
    records = [r.get('structured', {}) for r in reports]
    if any(not r.get('source_record_id') or not r.get('source') or
           r.get('status') not in ('ok', 'name_mapping_candidates') for r in records):
        return None, 'unresolved_sources'
    if records[0]['source_record_id'] == records[1]['source_record_id']:
        return None, 'same_source'
    if not records[0].get('dataset_version') or records[0]['dataset_version'] != records[1].get('dataset_version'):
        return None, 'version_mismatch'
    return {'records': [{k: r[k] for k in ('source_record_id', 'source')} for r in records],
            'version': records[0]['dataset_version']}, None


def verify_targets(data, selection):
    """Reject target rows without both source witnesses, not merely missing labels."""
    if data.get('status') != 'ok':
        return data
    try:
        ids = [r['source_record_id'] for r in selection['records']]
        if data['source_ids'] != ids or data['dataset_version'] != selection['version']:
            raise ValueError()
        items = data['items']
        if type(data['total']) is not int or data['total'] < len(items) or len(items) > 20:
            raise ValueError()
        seen = set()
        for item in items:
            if not item['identifier'] or item['identifier'] in seen:
                raise ValueError()
            seen.add(item['identifier'])
            if [s['source_record_id'] for s in item['sources']] != ids:
                raise ValueError()
            for source in item['sources']:
                if not source['witnesses'] or any(not w.get('source_pointer') or 'raw_row' not in w for w in source['witnesses']):
                    raise ValueError()
        if data['total'] and not items:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        return {'status': 'invalid_evidence'}
    return data


class PairState(TypedDict, total=False):
    left: dict
    right: dict
    targets: dict
    report: dict


async def run_pair_report(payload, *, on_state=None, researcher=run_report, target_backend=local_branch):
    from langgraph.graph import StateGraph, START, END
    names = validate_pair(payload['herbs'])
    question = payload['question']
    async def research(index):
        try:
            report = await researcher({'herb': names[index], 'question': question})
        except Exception:
            report = {'status': 'failed', 'herb': names[index], 'text': {}, 'structured': {}}
        return {('left', 'right')[index]: report}
    async def left(state): return await research(0)
    async def right(state): return await research(1)
    async def targets(state):
        selection, reason = source_pair([state['left'], state['right']])
        if reason:
            return {'targets': {'status': reason, 'executed': False}}
        try:
            result = await target_backend('shared_targets', '', json.dumps(selection, ensure_ascii=False))
            result = verify_targets(result, selection)
        except asyncio.TimeoutError:
            result = {'status': 'timeout'}
        except Exception:
            result = {'status': 'unavailable'}
        return {'targets': {**result, 'executed': True}}
    def assemble(state):
        reports = [state['left'], state['right']]
        rows = comparison_rows(reports)
        target = state['targets']
        complete = all(r['status'] == 'complete' for r in reports) and target['status'] == 'ok'
        status = 'complete' if complete else 'partial' if rows or target.get('items') else 'needs_input'
        return {'report': {'kind': 'pair_report', 'mode': 'local_dependent_evidence_graph', 'status': status,
                'herbs': names, 'question': question, 'rows': rows, 'targets': target,
                'source_reports': reports, 'identity_merged': False, 'answer_generated': False,
                'scope': '双方药典字段、ETCM基础属性及共同UniProt首页；未进行机制或疗效推断。'}}
    graph = StateGraph(PairState)
    graph.add_node('left_research', left); graph.add_node('right_research', right)
    graph.add_node('shared_target_query', targets); graph.add_node('comparison_review', assemble)
    graph.add_edge(START, 'left_research'); graph.add_edge(START, 'right_research')
    graph.add_edge(['left_research', 'right_research'], 'shared_target_query')
    graph.add_edge('shared_target_query', 'comparison_review'); graph.add_edge('comparison_review', END)
    state = {}; events = []
    async for update in graph.compile().astream({'left': {}, 'right': {}}, stream_mode='updates'):
        for name, values in update.items():
            if name not in ('left_research', 'right_research', 'shared_target_query', 'comparison_review') or not values:
                continue
            state.update(values)
            events.append({'tool': name, 'status': next(iter(values.values())).get('status', 'complete')})
            if on_state:
                # Count actual leaf queries, not the outer graph's node count.
                count = sum(2 for x in ('left', 'right') if state.get(x) and state[x].get('status') != 'failed')
                count += int(state.get('targets', {}).get('executed', False))
                await on_state({'last_node': name, 'tool_calls': count, 'model_calls': 0, 'events': list(events)})
    return state['report']
