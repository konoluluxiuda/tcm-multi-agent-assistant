"""Run isolated A/C tool protocols against identical synthetic local databases."""
import argparse
import asyncio
from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from scripts.check_pair_architecture_preflight import (
    RelationQueryTests, text_fixture, build, digest, verify_raw_witnesses, assess)
from tcm.cross_source_report import text_search, structured_search
from tcm.relation_queries import shared_targets
from tcm.target_similarity import file_token
from experiments.architecture_candidates.pair_protocol import PairProtocol, drivers


@contextmanager
def synthetic_fixture():
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
            entry['fields'].append({**entry['fields'][0], 'label': '注意',
                                    'text': '【注意】合成注意字段。', 'raw_text': '【注意】合成注意字段。'})
        path.write_text(json.dumps(view, ensure_ascii=False))
        summary = json.loads((source / 'summary.json').read_text())
        summary['research_view_sha256'] = digest(path)
        (source / 'summary.json').write_text(json.dumps(summary))
        with redirect_stdout(io.StringIO()):
            build(source, fixture.root / 'text-index')
        with patch.dict(os.environ, {'PHARMACOPOEIA_INDEX': str(fixture.root / 'text-index'),
                                    'LOCAL_DENSE_MODEL': '', 'LOCAL_DENSE_CACHE': '', 'LOCAL_RERANKER_MODEL': ''}):
            yield fixture
    finally:
        fixture.doCleanups()


def backend_for(fixture, fault=None):
    paths = [fixture.base, fixture.database, fixture.root / 'text-index/research.sqlite3',
             fixture.root / 'text-index/manifest.json']
    revision = [file_token(p) for p in paths]

    async def backend(name, args):
        if [file_token(p) for p in paths] != revision:
            raise ValueError('snapshot_changed')
        if name == 'read_source':
            herb, branch = args['herb'], args['branch']
            if fault == 'unavailable_side' and herb == 'Beta' and branch == 'structured':
                return {'status': 'unavailable'}
            result = text_search(herb, '功能与主治、注意事项') if branch == 'text' else structured_search(herb)
            if fault == 'missing_field' and herb == 'Beta' and branch == 'text':
                result['hits'] = [h for h in result['hits'] if h['label'] != '注意']
                result['status'] = 'partial_evidence'
        else:
            selection = args['selection']
            result = shared_targets(fixture.database, selection['records'], selection['version'],
                                    offset=(args['page'] - 1) * 20)
            result.update(status='ok', source_ids=[r['source_record_id'] for r in selection['records']],
                          dataset_version=selection['version'])
        if [file_token(p) for p in paths] != revision:
            raise ValueError('snapshot_changed')
        return result
    return backend


async def check():
    runs = []
    with synthetic_fixture() as fixture:
        for case, all_targets, fault in [('first_page', False, None), ('all_targets', True, None),
                                         ('missing_field', True, 'missing_field'),
                                         ('unavailable_side', True, 'unavailable_side')]:
            for candidate in ('A_protocol', 'C_protocol'):
                sdk, children = drivers(candidate, pages=2 if all_targets else 1)
                protocol = PairProtocol(('Alpha', 'Beta'), '比较功能与主治、注意事项和共同靶点',
                                        backend_for(fixture, fault), all_targets=all_targets)
                result = await protocol.run(candidate, sdk, children)
                result['case_id'] = case
                result['independent_assessment'] = assess(result, {'all_targets': all_targets})
                result['verified_target_witness_rows'] = verify_raw_witnesses(fixture.root, result)
                expected = 'partial' if fault else 'complete'
                if result['status'] != expected:
                    raise AssertionError((case, candidate, result['reason']))
                if result['independent_assessment']['business_complete'] != (fault is None):
                    raise AssertionError('Independent requirement check failed')
                if not fault and len(result['targets']['items']) != (23 if all_targets else 20):
                    raise AssertionError('Coverage differs')
                if children:
                    for herb, driver in children.items():
                        other = 'Beta' if herb == 'Alpha' else 'Alpha'
                        if other in json.dumps(driver.inputs):
                            raise AssertionError('Sibling context leaked')
                runs.append(result)
    return {'status': 'passed', 'runs': runs, 'live_model_calls': 0, 'data': 'synthetic_only',
            'limitations': ['Fixed SDK actions, no model planning or answer quality measurement',
                            'Isolated LangGraph adapters, not production A or full C deployment',
                            'No persistence, evidence offload or true parallel delegation',
                            'Schemas and full evidence inputs are not a measured token/cost comparison']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    report = asyncio.run(check())
    root = Path(__file__).resolve().parents[1]
    paths = [Path(__file__), root / 'scripts/check_pair_architecture_preflight.py',
             root / 'experiments/architecture_candidates/pair_protocol.py',
             root / 'tests/test_relation_queries.py', root / 'tests/test_mineru_retrieval.py',
             *(root / 'tcm').glob('*.py')]
    report['source_sha256'] = {str(p.relative_to(root)): digest(p) for p in paths}
    (args.out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'runs': len(report['runs']),
                      'complete': sum(r['status'] == 'complete' for r in report['runs'])}))


if __name__ == '__main__':
    main()
