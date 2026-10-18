"""Build reversible AI research revisions, leaving every source artifact unchanged."""
import argparse
import html
import json
import os
from pathlib import Path

from scripts.build_mineru_pharmacopoeia import inside, read, write
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_revisions import entry_identity, fingerprint, rebase_entries, rebased_target, render_view

ROOT = Path(__file__).resolve().parents[1]


def verified_corpus(base):
    summary = read(base / 'summary.json')
    for name in ('entries', 'blocks'):
        if digest(base / f'{name}.json') != summary[f'{name}_sha256']:
            raise ValueError(f'{name} changed since build')
    return summary, read(base / 'entries.json'), {b['id']: b for b in read(base / 'blocks.json')}


def build(base, baseline, review, output, disabled=(), without_overlays=False):
    if output.exists():
        raise FileExistsError('Use a fresh directory; sources and prior revisions are immutable')
    summary, parsed, blocks = verified_corpus(base)
    old_summary, old_parsed, old_blocks = verified_corpus(baseline)
    draft_path = review / 'correction_drafts.json'
    drafts = read(draft_path)
    frozen = read(ROOT / 'audits/mineru_review/summary.json')
    if (digest(draft_path) != frozen['correction_drafts_sha256']
            or digest(review / 'cases.json') != frozen['cases_sha256']
            or drafts['source_entries_sha256'] != old_summary['entries_sha256']
            or drafts['status'] != 'ai_proposals_not_applied'):
        raise ValueError('Review inputs changed since visual review')
    cases = {c['entry_id']: c for c in read(review / 'cases.json')['cases']}
    old_entries = {e['id']: e for e in old_parsed['entries']}
    entries = {e['id']: e for e in parsed['entries']}
    migration = rebase_entries(old_parsed['entries'], old_blocks, parsed['entries'], blocks)
    ledger = {'schema_version': 2, 'review_status': 'ai_proposed_pending_human_review',
              'human_signoff': False, 'scope': 'research_view_only',
              'entries_sha256': summary['entries_sha256'], 'blocks_sha256': summary['blocks_sha256'],
              'review_baseline_entries_sha256': old_summary['entries_sha256'],
              'source_drafts_sha256': digest(draft_path), 'source_cases_sha256': digest(review / 'cases.json'),
              'overlays': []}
    disabled = set(disabled)
    books = {}
    for index, proposal in enumerate(drafts['proposals'], 1):
        if proposal.get('human_signoff') is not False or proposal.get('applied') is not False:
            raise ValueError('Expected unchanged AI proposals')
        old_entry = old_entries[proposal['entry_id']]
        entry = entries[migration[proposal['entry_id']]]
        case = cases[proposal['entry_id']]
        matches = [p for p in case['previews'] if p['image'] == proposal['evidence_image']]
        if len(matches) != 1:
            raise ValueError('Revision image has no unique source receipt')
        evidence = matches[0]
        image_path = inside(review, evidence['image'])
        if digest(image_path) != evidence['sha256']:
            raise ValueError('Visual evidence image changed')
        book = evidence['source_document']
        if book in books and books[book] != evidence['source_sha256']:
            raise ValueError('Conflicting source book identities')
        books[book] = evidence['source_sha256']
        overlay_id = f'mineru-edit-{index:04}'
        ledger['overlays'].append({'id': overlay_id, 'entry_id': entry['id'],
                                  'source_proposal_entry_id': proposal['entry_id'],
                                  'entry_identity_sha256': fingerprint(entry_identity(entry)),
                                  'review_status': 'ai_proposed_pending_human_review', 'human_signoff': False,
                                  'enabled': not without_overlays and overlay_id not in disabled,
                                  'proposal': proposal, 'target': rebased_target(proposal, old_entry, old_blocks, entry, blocks),
                                  'evidence': {**evidence, 'review_directory': str(review.resolve())}})
    if disabled - {o['id'] for o in ledger['overlays']}:
        raise ValueError('Unknown overlay ID to disable')
    source_paths = {'high': ROOT / 'data/《中国药典》2025年版 一部.pdf',
                    'full': ROOT / 'data/《中国药典》2025年版 一部 全本.pdf'}
    for book, sha in books.items():
        if digest(source_paths[book]) != sha:
            raise ValueError('Source PDF changed since visual review')
    revised = render_view(parsed, blocks, ledger, summary['entries_sha256'], summary['blocks_sha256'])
    raw_ledger = {**ledger, 'overlays': [{**o, 'enabled': False} for o in ledger['overlays']]}
    raw_view = render_view(parsed, blocks, raw_ledger, summary['entries_sha256'], summary['blocks_sha256'])
    # All checks finish before publishing a new local artifact directory.
    output.mkdir(parents=True)
    write(output / 'revision_ledger.json', ledger)
    write(output / 'research_view.json', revised)
    write(output / 'original_view.json', raw_view)
    write(output / 'source_rebase.json', {'old_entries_sha256': old_summary['entries_sha256'],
                                         'new_entries_sha256': summary['entries_sha256'],
                                         'mapping': migration, 'human_signoff': False})
    active = [o for o in ledger['overlays'] if o['enabled']]
    report = {'kind': 'mineru_research_revisions', 'schema_version': 2,
              'status': 'ai_revisions_applied_to_research_view', 'human_signoff': False,
              'production_ingested': False, 'new_ocr_pages': 0,
              'entries': len(revised['entries']), 'overlay_proposals': len(ledger['overlays']),
              'active_overlays': len(active), 'revised_entries': sum(bool(e['applied_overlay_ids']) for e in revised['entries']),
              'source_entries_sha256': summary['entries_sha256'], 'source_blocks_sha256': summary['blocks_sha256'],
              'ledger_sha256': digest(output / 'revision_ledger.json'),
              'research_view_sha256': digest(output / 'research_view.json'),
              'original_view_sha256': digest(output / 'original_view.json'),
              'source_rebase_sha256': digest(output / 'source_rebase.json'),
              'revision_code_sha256': digest(ROOT / 'tcm/mineru_revisions.py'),
              'builder_code_sha256': digest(Path(__file__))}
    write(output / 'summary.json', report)
    lines = ['# MinerU研究修订（AI待核实）', '',
             '修订仅用于本地研究视图，原始识别文件保留。关闭某条覆盖或全部覆盖可恢复原始候选。', '',
             '| 修订 | 条目 | 类型 | 原值 | 研究视图值 | 启用 |', '|---|---|---|---|---|---|']
    sections = ['<!doctype html><meta charset="utf-8"><title>MinerU修订对照</title>',
                '<style>body{max-width:1000px;margin:24px auto;font:16px sans-serif}img{max-width:100%}pre{white-space:pre-wrap}section{border-top:1px solid #aaa;padding:16px 0}</style>',
                '<h1>MinerU修订对照：AI待核实，仅用于研究视图</h1>']
    for o in ledger['overlays']:
        t = o['target']
        before = json.dumps(t['before'], ensure_ascii=False)
        after = json.dumps(t['after'], ensure_ascii=False)
        lines.append(f"| {o['id']} | {o['entry_id']} | {t['kind']} | {before} | {after} | {o['enabled']} |")
        evidence = o['evidence']
        relative = os.path.relpath(Path(evidence['review_directory']) / evidence['image'], output)
        sections.append(f'<section><h2>{html.escape(o["id"] + " / " + o["entry_id"])}</h2>'
                        f'<p>章节{evidence["chapter_page"]}，原PDF物理页{evidence["source_pdf_page"]}；启用：{o["enabled"]}</p>'
                        f'<pre>{html.escape(before)} → {html.escape(after)}</pre>'
                        f'<img src="{html.escape(relative, quote=True)}"></section>')
    (output / 'revision_diff.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (output / 'index.html').write_text('\n'.join(sections), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT / 'var/mineru_pharmacopoeia_v4_checked')
    parser.add_argument('--baseline', type=Path, default=ROOT / 'var/mineru_pharmacopoeia_v2')
    parser.add_argument('--review', type=Path, default=ROOT / 'var/mineru_review_v2')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/mineru_revisions_v2')
    parser.add_argument('--disable', action='append', default=[], help='Overlay ID; may be repeated')
    parser.add_argument('--without-overlays', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.baseline, args.review, args.out,
                           args.disable, args.without_overlays), ensure_ascii=False))


if __name__ == '__main__':
    main()
