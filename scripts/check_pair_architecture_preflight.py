"""Compare flat execution and existing nested graphs, not autonomous agent quality."""
import argparse
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from tests.test_relation_queries import RelationQueryTests
from tests.test_mineru_retrieval import fixture as text_fixture
from scripts.build_mineru_retrieval import build
from tcm.mineru_pharmacopoeia import digest
from tcm.cross_source_report import text_search, structured_search, assemble_report, run_report
from tcm.pair_research_report import run_pair_report, source_pair, verify_targets, comparison_rows
from tcm.relation_queries import shared_targets


CASES = [
    {'id': 'first_page', 'question': '比较功能与主治、注意事项和共同靶点首页',
     'all_targets': False, 'fault': None, 'expected_complete': True},
    {'id': 'all_targets', 'question': '比较功能与主治、注意事项，列出全部共同靶点',
     'all_targets': True, 'fault': None, 'expected_complete': False},
    {'id': 'missing_field', 'question': '比较功能与主治、注意事项和共同靶点首页',
     'all_targets': False, 'fault': 'missing_field', 'expected_complete': False},
    {'id': 'unavailable_side', 'question': '比较功能与主治、注意事项和共同靶点首页',
     'all_targets': False, 'fault': 'unavailable_side', 'expected_complete': False},
]


def assess(report, case):
    """Requirements are independent of a graph's self-reported complete flag."""
    sides = report['source_reports']
    fields = all(all(any(h['label'] == label and h.get('evidence') for h in
                        side.get('text', {}).get('hits', []))
                     for label in ('功能与主治', '注意')) for side in sides)
    sources = all(side.get('structured', {}).get('source_record_id') for side in sides)
    targets = report['targets']
    count = len(targets.get('items', []))
    coverage = targets.get('status') == 'ok' and (
        count == targets.get('total') if case['all_targets'] else count > 0)
    evidence = all(len(item.get('sources', [])) == 2 and
                   all(s.get('witnesses') for s in item['sources'])
                   for item in targets.get('items', []))
    requirements = {'both_requested_text_fields': fields, 'both_structured_sources': bool(sources),
                    'requested_target_coverage': coverage, 'target_witnesses': evidence and count > 0}
    return {'requirements': requirements, 'business_complete': all(requirements.values()),
            'reported_status': report['status'], 'returned_targets': count,
            'total_targets': targets.get('total'),
            'false_complete': report['status'] == 'complete' and not all(requirements.values())}


async def execute(candidate, case):
    calls = []

    async def backend(branch, herb, question):
        calls.append({'branch': branch, 'herb': herb})
        if case['fault'] == 'unavailable_side' and herb == 'Beta' and branch == 'structured':
            raise ValueError('Injected local source failure')
        if branch == 'text':
            result = text_search(herb, question)
            if case['fault'] == 'missing_field' and herb == 'Beta':
                result['hits'] = [h for h in result.get('hits', []) if h['label'] != '注意']
                result['status'] = 'partial_evidence'
            return result
        if branch == 'structured':
            return structured_search(herb)
        selection = json.loads(question)
        result = shared_targets(os.environ['ETCM_ENRICHMENT_DB'], selection['records'], selection['version'],
                                offset=selection.get('offset', 0))
        return {**result, 'status': 'ok', 'source_ids': [r['source_record_id'] for r in selection['records']],
                'dataset_version': selection['version']}

    payload = {'herbs': ['Alpha', 'Beta'], 'question': case['question']}
    if candidate == 'existing_subgraphs':
        async def researcher(p):
            return await run_report(p, backend=backend)
        report = await run_pair_report(payload, researcher=researcher, target_backend=backend)
    elif candidate == 'flat_reference':
        sides = []
        for herb in payload['herbs']:
            state = {}
            for branch in ('text', 'structured'):
                try:
                    state[branch] = await backend(branch, herb, case['question'])
                except ValueError:
                    state[branch] = {'status': 'unavailable'}
            sides.append(assemble_report(state, herb, case['question'])['report'])
        selection, reason = source_pair(sides)
        target = {'status': reason, 'executed': False} if reason else {
            **verify_targets(await backend('shared_targets', '', json.dumps(selection)), selection),
            'executed': True}
        rows = comparison_rows(sides)
        complete = all(s['status'] == 'complete' for s in sides) and target['status'] == 'ok'
        report = {'source_reports': sides, 'rows': rows, 'targets': target,
                  'status': 'complete' if complete else 'partial' if rows else 'needs_input'}
    else:
        raise ValueError('Unknown candidate')
    return {'candidate': candidate, 'case_id': case['id'], 'assessment': assess(report, case),
            'tool_calls': len(calls), 'trace': calls, 'report': report, 'live_model_calls': 0}


def verify_raw_witnesses(root, report):
    records = {s['structured']['source_record_id']: s['structured'] for s in report['source_reports']
               if s.get('structured', {}).get('source_record_id')}
    count = 0
    for item in report['targets'].get('items', []):
        for side in item['sources']:
            record = records[side['source_record_id']]
            path = root / 'source' / record['source']['relative_file_path']
            if digest(path) != record['source']['file_sha256']:
                raise AssertionError('Source hash differs')
            for witness in side['witnesses']:
                value = json.loads(path.read_text())
                for token in witness['source_pointer'].split('/')[1:]:
                    value = value[int(token)] if isinstance(value, list) else value[token]
                if value != witness['raw_row']:
                    raise AssertionError('Source pointer differs')
                count += 1
    return count


async def check():
    fixture = RelationQueryTests()
    try:
        fixture.setUp()
        source = fixture.root / 'text-source'
        text_fixture(source)
        path = source / 'research_view.json'
        view = json.loads(path.read_text())
        for entry, name in zip(view['entries'], ('Alpha', 'Beta')):
            entry.update(name_zh_candidate=name, raw_name_zh_candidate=name)
            entry['fields'] = [f for f in entry['fields'] if f['label'] == '功能与主治']
            caution = dict(entry['fields'][0])
            caution.update(label='注意', text='【注意】合成注意字段。', raw_text='【注意】合成注意字段。')
            entry['fields'].append(caution)
        path.write_text(json.dumps(view, ensure_ascii=False))
        summary = json.loads((source / 'summary.json').read_text())
        summary['research_view_sha256'] = digest(path)
        (source / 'summary.json').write_text(json.dumps(summary))
        with redirect_stdout(io.StringIO()):
            build(source, fixture.root / 'text-index')
        env = {'PHARMACOPOEIA_INDEX': str(fixture.root / 'text-index'),
               'LOCAL_DENSE_MODEL': '', 'LOCAL_DENSE_CACHE': '', 'LOCAL_RERANKER_MODEL': ''}
        runs = []
        with patch.dict(os.environ, env):
            for case in CASES:
                pair = [await execute(candidate, case) for candidate in ('flat_reference', 'existing_subgraphs')]
                for run in pair:
                    run['verified_target_witness_rows'] = verify_raw_witnesses(fixture.root, run['report'])
                    expected = case['expected_complete'] or (
                        case['id'] == 'all_targets' and run['candidate'] == 'existing_subgraphs')
                    if run['assessment']['business_complete'] != expected:
                        raise AssertionError(json.dumps(run['assessment']))
                if case['id'] != 'all_targets' and pair[0]['assessment'] != pair[1]['assessment']:
                    raise AssertionError('Unexpected topology difference')
                runs.extend(pair)
        return {'status': 'passed', 'meaning': 'preflight checks passed; not all business tasks completed',
                'data': 'synthetic_only', 'live_model_calls': 0, 'cases': CASES, 'runs': runs,
                'limitations': ['No model supervisor or model loop tested',
                                'Flat reference is not candidate A production runtime',
                                'Existing nested graph is not candidate C autonomous supervisor',
                                'Synthetic text provenance is fixture metadata, not a real PDF',
                                'No latency, token or cost advantage measured']}
    finally:
        fixture.doCleanups()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = asyncio.run(check())
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__), root / 'tests/test_relation_queries.py', root / 'tests/test_mineru_retrieval.py',
             *(root / 'tcm').glob('*.py'), root / 'scripts/build_mineru_retrieval.py',
             root / 'scripts/build_etcm_enrichment.py']
    report['source_sha256'] = {str(p.relative_to(root)): digest(p) for p in paths}
    (args.out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'runs': len(report['runs']),
                      'business_complete': sum(r['assessment']['business_complete'] for r in report['runs']),
                      'false_complete': sum(r['assessment']['false_complete'] for r in report['runs'])}))


if __name__ == '__main__':
    main()
