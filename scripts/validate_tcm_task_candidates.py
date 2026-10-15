"""Validate AI-designed task candidates and source anchors; never execute tasks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

QUOTAS = {'detail': 15, 'filter': 10, 'compare': 10, 'boundary': 10,
          'sparse_evidence': 10, 'multi_step': 5}
REVIEW = 'ai_draft_pending_human_review'
ROOT = Path(__file__).resolve().parents[1]


def validate(suite, source=None):
    errors = []

    def require(condition, message):
        if not condition:
            errors.append(message)

    require(suite.get('kind') == 'task_candidates', 'kind must be task_candidates')
    require(suite.get('origin') == 'designed', 'suite origin must be designed')
    require(suite.get('review_status') == REVIEW, 'suite cannot claim human approval')
    require(suite.get('observed_question_count') == 0, 'designed set cannot claim observed questions')
    cases = suite.get('cases', [])
    require(isinstance(cases, list), 'cases must be a list')
    if not isinstance(cases, list):
        cases = []
    counts = {category: 0 for category in QUOTAS}
    splits = {'dev': 0, 'test': 0}
    evidence_focus = {'sparse': 0, 'pmid': 0}
    capabilities = {'supported': 0, 'unsupported': 0}
    groups, anchors, ids, questions = {}, {}, set(), set()
    cache = {}
    source_checks = 0
    for case in cases:
        identifier = case.get('id')
        require(isinstance(identifier, str) and identifier not in ids, f'duplicate/invalid ID: {identifier}')
        ids.add(identifier)
        question = case.get('question', '')
        require(isinstance(question, str) and bool(question.strip()) and question not in questions,
                f'{identifier}: empty/duplicate question')
        questions.add(question)
        category = case.get('category')
        require(category in counts, f'{identifier}: unknown category')
        if category in counts:
            counts[category] += 1
        if category == 'sparse_evidence':
            focus = case.get('evidence_focus')
            require(focus in evidence_focus, f'{identifier}: evidence_focus must be sparse or pmid')
            if focus in evidence_focus:
                evidence_focus[focus] += 1
        split = case.get('split')
        require(split in splits, f'{identifier}: unknown split')
        if split in splits:
            splits[split] += 1
        require(case.get('origin') == 'designed' and case.get('review_status') == REVIEW,
                f'{identifier}: must remain an AI-designed draft')
        group = case.get('group_id')
        require(isinstance(group, str) and bool(group), f'{identifier}: group_id missing')
        if group in groups:
            require(groups[group] == split, f'{identifier}: group crosses dev/test')
        groups[group] = split
        keys = case.get('leakage_keys', [])
        require(isinstance(keys, list) and bool(keys) and all(isinstance(k, str) for k in keys),
                f'{identifier}: explicit leakage_keys required')
        for key in keys:
            if key in anchors:
                require(anchors[key] == split, f'{identifier}: leakage anchor crosses dev/test: {key}')
            anchors[key] = split
        supported = case.get('capability_status')
        mode = case.get('execution_mode')
        require(supported in ('supported', 'unsupported'), f'{identifier}: capability status missing')
        if supported in capabilities:
            capabilities[supported] += 1
        require(mode in ('api', 'manual_ui', 'planned_chat'), f'{identifier}: execution mode invalid')
        require(not (mode == 'planned_chat' and supported == 'supported'),
                f'{identifier}: planned chat cannot claim support')
        for key in ('input', 'expected', 'source_evidence', 'limitations', 'execution_instructions'):
            require(key in case, f'{identifier}: missing {key}')
        evidence = case.get('source_evidence', [])
        require(isinstance(evidence, list), f'{identifier}: source_evidence must be list')
        if source is None or not isinstance(evidence, list):
            continue
        for item in evidence:
            relative = item.get('relative_path', '')
            path = (source / relative).resolve()
            require(bool(relative) and path.is_relative_to(source.resolve()), f'{identifier}: unsafe source path')
            if not relative or not path.is_relative_to(source.resolve()):
                continue
            try:
                if relative not in cache:
                    raw = path.read_bytes()
                    cache[relative] = (hashlib.sha256(raw).hexdigest(), json.loads(raw))
                digest, value = cache[relative]
                require(item.get('sha256') == digest, f'{identifier}: source hash mismatch: {relative}')
                pointer = item.get('json_pointer')
                require(isinstance(pointer, str) and pointer.startswith('/'), f'{identifier}: pointer missing')
                if not isinstance(pointer, str) or not pointer.startswith('/'):
                    continue
                for token in pointer[1:].split('/'):
                    token = token.replace('~1', '/').replace('~0', '~')
                    value = value[int(token)] if isinstance(value, list) else value[token]
                require('raw_value' in item and value == item['raw_value'],
                        f'{identifier}: raw value mismatch: {relative}{pointer}')
                source_checks += 1
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                errors.append(f'{identifier}: source evidence unreadable ({type(exc).__name__})')
    require(counts == QUOTAS, 'category quotas must be 15/10/10/10/10/5')
    require(splits == {'dev': 20, 'test': 40}, 'split must be dev20/test40')
    require(evidence_focus == {'sparse': 5, 'pmid': 5}, 'sparse_evidence must include sparse5/pmid5')
    if source is not None:
        snapshot = suite.get('source_snapshot', {})
        entries = [[p.relative_to(source).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()]
                   for p in sorted((source / 'etcm_herbs').glob('*.json'))]
        digest = hashlib.sha256(json.dumps(entries, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        require(bool(entries) and snapshot.get('record_count') == len(entries)
                and snapshot.get('sha256') == digest, 'source snapshot binding mismatch')
    return {'status': 'passed' if not errors else 'failed', 'candidate_count': len(cases),
            'category_counts': counts, 'splits': splits, 'source_anchor_checks': source_checks,
            'evidence_focus_counts': evidence_focus, 'capability_declarations': capabilities,
            'source_validation': 'not_run' if source is None else 'checked',
            'errors': errors, 'task_execution': 'not_run', 'human_review': 'pending',
            'limitations': 'Format, declared split anchors and source consistency only; not answer completeness, scientific validity or task accuracy.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT / 'eval/tcm_task_candidates_v1.json')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--out', type=Path, default=ROOT / 'var/task_candidates/validation.json')
    args = parser.parse_args()
    if args.out.resolve() == args.suite.resolve() or (args.source and args.out.resolve().is_relative_to(args.source.resolve())):
        parser.error('output must not overwrite suite or enter source root')
    report = validate(json.loads(args.suite.read_text()), args.source)
    report['suite_sha256'] = hashlib.sha256(args.suite.read_bytes()).hexdigest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
