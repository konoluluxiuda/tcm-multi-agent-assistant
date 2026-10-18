"""Rebase frozen locator references by unchanged source evidence, not ordinal IDs."""
import argparse
import json
from pathlib import Path

from scripts.build_mineru_pharmacopoeia import read, write
from scripts.build_mineru_revisions import verified_corpus
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_revisions import fingerprint, rebase_entries

ROOT=Path(__file__).resolve().parents[1]


def rebase(old_base,new_base,old_revisions,new_revisions,references,questions,output):
    if output.exists():raise FileExistsError('Preserve old references; select a new output file')
    old_summary,old_parsed,old_blocks=verified_corpus(old_base)
    new_summary,new_parsed,new_blocks=verified_corpus(new_base)
    mapping=rebase_entries(old_parsed['entries'],old_blocks,new_parsed['entries'],new_blocks)
    refs=read(references)
    if refs['question_sha256']!=digest(questions):raise ValueError('Frozen questions changed')
    views=[]
    for directory,summary in [(old_revisions,old_summary),(new_revisions,new_summary)]:
        receipt=read(directory/'summary.json');path=directory/'research_view.json'
        if digest(path)!=receipt['research_view_sha256']:raise ValueError('Revision view changed')
        view=read(path)
        if view['entries_sha256']!=summary['entries_sha256']:raise ValueError('Revision corpus mismatch')
        views.append({e['id']:e for e in view['entries']})
    old_view,new_view=views;result=[]
    for ref in refs['references']:
        previous=old_view[ref['entry_id']]['fields'][ref['field_index']]
        entry=new_view[mapping[ref['entry_id']]]
        candidates=[(i,f) for i,f in enumerate(entry['fields']) if f['label']==ref['label']
                    and previous['evidence'][0] in f['evidence']]
        if len(candidates)!=1 or entry['name_zh_candidate']!=ref['name']:
            raise ValueError('Reference evidence no longer has one matching field/name')
        i,field=candidates[0]
        result.append({**ref,'previous_entry_id':ref['entry_id'],'previous_field_index':ref['field_index'],
                       'entry_id':entry['id'],'field_index':i,
                       'field_occurrences':sum(f['label']==ref['label'] and not f['scope'].startswith('appendix:')
                                               for f in entry['fields'])})
    updated={**refs,'references':result,'reference_status':'source_assisted_ai_reference_rebased_not_new_gold',
             'rebase':{'previous_reference_sha256':digest(references),
                       'old_entries_sha256':old_summary['entries_sha256'],
                       'new_entries_sha256':new_summary['entries_sha256'],
                       'source_mapping_fingerprint':fingerprint(mapping),
                       'recipe':'same source title and same field first evidence span',
                       'code_sha256':digest(Path(__file__))}}
    write(output,updated)
    return {'references_rebased':len(result),'questions_changed':False,'reference_sha256':digest(output)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--old-base',type=Path,default=ROOT/'var/mineru_pharmacopoeia_v3')
    p.add_argument('--new-base',type=Path,default=ROOT/'var/mineru_pharmacopoeia_v4_checked')
    p.add_argument('--old-revisions',type=Path,default=ROOT/'var/mineru_revisions_v1')
    p.add_argument('--new-revisions',type=Path,default=ROOT/'var/mineru_revisions_v2')
    p.add_argument('--references',type=Path,default=ROOT/'eval/mineru_retrieval_reference_v1.json')
    p.add_argument('--questions',type=Path,default=ROOT/'eval/mineru_retrieval_questions_v1.json')
    p.add_argument('--out',type=Path,default=ROOT/'eval/mineru_retrieval_reference_v2.json')
    a=p.parse_args();print(json.dumps(rebase(a.old_base,a.new_base,a.old_revisions,a.new_revisions,
                                           a.references,a.questions,a.out),ensure_ascii=False))
