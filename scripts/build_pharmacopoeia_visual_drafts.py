"""Package AI visual transcriptions as a separate, unapproved local review artifact."""
import argparse
import json
from pathlib import Path
import shutil

from tcm.pharmacopoeia import digest
from tcm.pharmacopoeia_comparison import attach_visual_drafts, review_template

ROOT = Path(__file__).resolve().parents[1]


def build(base, proposal_path, output):
    if output.resolve() == base.resolve() or output.exists():
        raise ValueError('Use a new output directory; preserve base and previous drafts')
    source = base/'comparison.json'
    proposal = json.loads(proposal_path.read_text())
    data = json.loads(source.read_text())
    hashes = {p['image']: digest(base/p['image']) for r in data['records'] for f in r['fields'] for p in f['previews']}
    hashes.update({p['image']: digest(base/p['image']) for r in data['records'] for p in r['region_previews']})
    for document in data['documents'].values():
        if digest(ROOT/document['path']) != document['sha256']:
            raise ValueError('Source PDF changed')
    result = attach_visual_drafts(data, proposal, digest(source), hashes)
    output.mkdir(parents=True)
    shutil.copytree(base/'assets', output/'assets')
    (output/'index.html').write_text((ROOT/'tcm/pharmacopoeia_comparison.html').read_text(), encoding='utf-8')
    (output/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    result['artifact_sha256'] = digest(output/'comparison.json')
    (output/'review_template.json').write_text(json.dumps(review_template(result), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    summary = {**result['visual_draft_provenance'], 'comparison_sha256': result['artifact_sha256'],
               'proposal_sha256': digest(proposal_path), 'source_pdf_authenticity_verified': False,
               'excluded_margin_lines': sum(len(items) for r in result['records'] for items in r['excluded_margin_lines'].values()),
               'review_template_sha256': digest(output/'review_template.json'),
               'code_sha256': {name: digest(ROOT/name) for name in [
                   'tcm/pharmacopoeia_comparison.py', 'tcm/pharmacopoeia_comparison.html',
                   'scripts/build_pharmacopoeia_visual_drafts.py']}}
    (output/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=ROOT/'var/pharmacopoeia_dual_review_margin_fixed')
    parser.add_argument('--proposal', type=Path, default=ROOT/'var/pharmacopoeia_visual_transcription_input.json')
    parser.add_argument('--out', type=Path, default=ROOT/'var/pharmacopoeia_visual_triage')
    args = parser.parse_args()
    build(args.base, args.proposal, args.out)
