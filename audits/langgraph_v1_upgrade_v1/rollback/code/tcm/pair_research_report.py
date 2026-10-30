"""Two independent research subgraphs followed by source-bound target comparison."""
import asyncio
import json
import time
import unicodedata
import re
from typing import TypedDict

from tcm.cross_source_report import run_report, local_branch, assemble_report

MAX_TARGET_PAGES = 10


def requires_all_targets(question):
    # Bind completeness to the target clause, not to an unrelated "all fields" request.
    return bool(re.search(r'(?<!不需要)(?<!不要)(?<!不必)(?:全部|所有|完整的?)(?:的)?共同(?:靶点|标识)|共同(?:靶点|标识)(?:的)?(?:全部|所有|完整列表)', question))


def merge_target_page(previous, page, selection):
    """Validate a source-bound page before admitting it into a durable aggregate."""
    checked = verify_targets(page, selection)
    if checked.get('status') != 'ok':
        raise ValueError('invalid_evidence')
    offset = len(previous.get('items', []))
    total = page['total']
    count = min(20, max(0, total - offset))
    expected_next = offset + 20 if offset + 20 < total else None
    prior_ids = {item['identifier'] for item in previous.get('items', [])}
    if (page.get('offset') != offset or page.get('limit') != 20 or
            page.get('returned_count') != count or len(page['items']) != count or
            page.get('next_offset') != expected_next or not page.get('catalog_revision') or
            previous and (page['total'] != previous['total'] or
                          page['catalog_revision'] != previous['catalog_revision']) or
            prior_ids.intersection(i['identifier'] for i in page['items'])):
        raise ValueError('invalid_pagination_or_revision')
    items = previous.get('items', []) + page['items']
    return {**page, 'offset': 0, 'items': items, 'returned_count': len(items),
            'pages_read': previous.get('pages_read', 0) + 1}


def missing_requested_fields(reports, names, question):
    from tcm.report_request import interpret_report_request
    missing = []
    for name, report in zip(names, reports):
        wanted = interpret_report_request(name, question)['fields']
        observed = {h['label'] for h in report.get('text', {}).get('hits', []) if h.get('evidence')}
        if observed & {'性味', '性味与归经'}:
            observed.update(('性味', '性味与归经'))
        missing.extend({'herb': name, 'field': field} for field in wanted if field not in observed)
    return missing


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
    contract: dict
    left_text: dict
    left_structured: dict
    right_text: dict
    right_structured: dict
    left: dict
    right: dict
    targets: dict
    report: dict


async def run_pair_report(payload, *, on_state=None, researcher=run_report, target_backend=local_branch,
                          checkpointer=None, thread_id=None, branch_backend=None):
    from langgraph.graph import StateGraph, START, END
    names = validate_pair(payload['herbs'])
    question = payload['question']
    all_targets = requires_all_targets(question)
    contract = {'version': 2, 'herbs': names, 'question': question, 'all_targets': all_targets,
                'max_target_pages': MAX_TARGET_PAGES}
    if checkpointer and (not thread_id or researcher is not run_report):
        raise ValueError('Durable pair graph requires a private thread and standard leaf contract')
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
        previous = state.get('targets', {})
        calls = previous.get('page_calls', 0) + 1
        try:
            request = {**selection, 'offset': len(previous.get('items', []))} if all_targets else selection
            result = await target_backend('shared_targets', '', json.dumps(request, ensure_ascii=False))
            if all_targets:
                result = merge_target_page(previous, result, selection)
                if result['next_offset'] is not None and calls >= MAX_TARGET_PAGES:
                    result['status'] = 'page_budget_exhausted'
            else:
                result = verify_targets(result, selection)
        except asyncio.TimeoutError:
            result = {**previous, 'status': 'timeout'}
        except ValueError:
            result = {**previous, 'status': 'invalid_evidence'}
        except Exception:
            result = {**previous, 'status': 'unavailable'}
        return {'targets': {**result, 'executed': True, 'page_calls': calls}}
    def assemble(state):
        reports = [state['left'], state['right']]
        rows = comparison_rows(reports)
        target = state['targets']
        missing = missing_requested_fields(reports, names, question)
        target_complete = target['status'] == 'ok' and (not all_targets or
            target.get('next_offset') is None and len(target.get('items', [])) == target.get('total'))
        complete = all(r['status'] == 'complete' for r in reports) and target_complete and not missing
        status = 'complete' if complete else 'partial' if rows or target.get('items') else 'needs_input'
        return {'report': {'kind': 'pair_report', 'mode': 'local_dependent_evidence_graph', 'status': status,
                'herbs': names, 'question': question, 'rows': rows, 'targets': target,
                'source_reports': reports, 'identity_merged': False, 'answer_generated': False,
                'completion': {'all_targets_requested': all_targets, 'target_coverage_complete': target_complete,
                               'missing_fields': missing},
                'scope': ('双方药典字段、ETCM基础属性及全部共同UniProt（未完成时保留已读结果）；' if all_targets else
                          '双方药典字段、ETCM基础属性及共同UniProt首页；') + '未进行机制或疗效推断。'}}
    graph = StateGraph(PairState)
    leaf_nodes = []
    if checkpointer:
        from tcm.mcp_client import report_branch
        backend = branch_backend or report_branch
        # Persist each read independently: a half-finished herb must not replay its completed read.
        def leaf(side, index, branch):
            async def query(state):
                started = time.monotonic()
                try:
                    value = await backend(branch, names[index], question)
                except asyncio.TimeoutError:
                    value = {'status':'timeout'}
                except Exception:
                    value = {'status':'unavailable'}
                return {side+'_'+branch:{**value,'duration_ms':round((time.monotonic()-started)*1000)}}
            return query
        def side_report(side, index):
            def assemble_side(state):
                result = assemble_report({key:state[side+'_'+key] for key in ('text','structured')},names[index],question)
                return {side:result['report']}
            return assemble_side
        for index,side in enumerate(('left','right')):
            nodes = [side+'_text_query',side+'_structured_query']
            for branch,node in zip(('text','structured'),nodes):
                graph.add_node(node,leaf(side,index,branch));graph.add_edge(START,node)
            graph.add_node(side+'_research',side_report(side,index))
            graph.add_edge(nodes,side+'_research');leaf_nodes.extend(nodes)
    else:
        graph.add_node('left_research', left); graph.add_node('right_research', right)
        graph.add_edge(START, 'left_research'); graph.add_edge(START, 'right_research')
    graph.add_node('shared_target_query', targets); graph.add_node('comparison_review', assemble)
    graph.add_edge(['left_research', 'right_research'], 'shared_target_query')
    graph.add_conditional_edges('shared_target_query', lambda s: 'shared_target_query' if
        all_targets and s['targets'].get('status') == 'ok' and s['targets'].get('next_offset') is not None
        else 'comparison_review')
    graph.add_edge('comparison_review', END)
    compiled=graph.compile(checkpointer=checkpointer)
    config={'configurable':{'thread_id':thread_id}} if checkpointer else None
    initial={'left': {}, 'right': {}, 'contract': contract}
    state = {}; events = []
    if checkpointer:
        previous=await compiled.aget_state(config)
        if previous.values:
            if previous.values.get('contract') != contract:
                raise ValueError('Pair report execution contract changed')
            state=dict(previous.values);initial=None
            if not previous.next and state.get('report'):return state['report']
    # LangGraph 0.3 writes checkpoints asynchronously. Exit the stream at each
    # page boundary so pending writes finish before dispatching the next page.
    while True:
        async for update in compiled.astream(initial, config=config, stream_mode='updates',
            interrupt_after=['shared_target_query'] if checkpointer and all_targets else None):
            for name, values in update.items():
                if name not in (*leaf_nodes, 'left_research', 'right_research', 'shared_target_query', 'comparison_review') or not values:
                    continue
                state.update(values)
                events.append({'tool': name, 'status': next(iter(values.values())).get('status', 'complete')})
                if on_state:
                    # Count actual leaf queries, not the outer graph's node count.
                    count = (sum(bool(state.get(key.removesuffix('_query'))) for key in leaf_nodes) if checkpointer else
                             sum(2 for x in ('left', 'right') if state.get(x) and state[x].get('status') != 'failed'))
                    count += state.get('targets', {}).get('page_calls', 0)
                    await on_state({'last_node': name, 'tool_calls': count, 'model_calls': 0, 'events': list(events)})
        if not (checkpointer and all_targets):
            break
        saved = await compiled.aget_state(config)
        state.update(saved.values)
        if not saved.next:
            break
        initial = None
    return state['report']
