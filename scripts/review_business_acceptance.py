"""Offline contract review of the first saved business screening; makes no requests.

Preserves original scores. Corrections are explicitly identified as author review,
not an independent evaluation or application improvement.
"""
import argparse
from collections import Counter
import copy
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil

from scripts.eval_business_acceptance import SOURCE, SourceChecks, sha
from scripts.eval_local_usability import table_rows


class TextOnly(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
    def handle_data(self,data):self.parts.append(data)


def review(batch):
    original=json.loads((batch/'summary.json').read_text())
    raw={r['id']:r for r in json.loads((batch/'raw_results.json').read_text())}
    reference=json.loads((batch/'references.json').read_text())
    refs=SourceChecks(reference);corrected=copy.deepcopy(original);changes=[]
    def body(id):return raw[id]['turns'][-1]['body']
    def correction(id,outcome,reason):
        row=next(r for r in corrected['cases'] if r['id']==id)
        changes.append({'id':id,'original_outcome':row['outcome'],'reviewed_outcome':outcome,'basis':reason})
        row.update(outcome=outcome,reason=reason)
    # ADR-0001 predates this batch. Raw HTML must survive in provenance; display strips tags.
    herb=body('P17')['tcm'];assert herb['status']=='ok' and len(herb['records'])==1
    field=herb['records'][0]['fields']['english_name'];src=field['source']
    assert src['relative_file_path']=='etcm_herbs/HuangQi.json'
    assert src['raw_value']==reference['herb_fields']['P17']
    refs.pointer(src['relative_file_path'],src['json_pointer'],src['raw_value'],src['file_sha256'])
    parser=TextOnly();parser.feed(src['raw_value']);assert field['value']==''.join(parser.parts).strip()
    correction('P17','completed_local_scope','ADR-0001 display strips HTML; original raw value and JSON pointer match. Scientific meaning of the source field is not validated.')
    # A valid page beyond the final row should be empty, not fabricated or counted as failure.
    relation=body('P18')['assistant']['relations'];data=relation['data']
    assert relation['target']['uniprot']=='Q07973' and relation['page']==2
    assert data['total']==reference['counts']['P18']==11 and data['offset']==20
    assert data['items']==[] and data['next_offset'] is None
    target=relation['target'];path=SOURCE/target['source_file'];assert sha(path)==target['source_file_sha256']
    source=json.loads(path.read_text());assert len(table_rows(source,'ingredient'))==11
    correction('P18','completed_local_scope','Requested second page correctly returns offset20, total11, no rows and no next page; original target JSON independently has11 ingredient rows.')
    # Preserve specific-state deviation: the product asks for a name, but does not expose a missing-focus status.
    research=body('P14')['research']
    assert research['status']=='name_not_resolved' and not (research.get('tool_result') or {}).get('hits')
    assert '药名' in research['message']
    correction('P14','appropriate_clarification','Business-level name clarification is acceptable; exact preregistered clarify_entity status was not met. Record this deviation and weaker context diagnosis.')
    # This is an incomplete positive task, not a malformed relation result: no relation query actually ran.
    assistant=body('P20')['assistant']
    assert assistant['status']=='clarification_needed' and assistant['topic']=='relation_source_needed'
    assert not assistant.get('relations',{}).get('data')
    correction('P20','clarification','Source guidance intercepted a positive relation-plus-provenance request; no relation data returned. Task remains incomplete, not a source-row verification failure.')
    positives=[r for r in corrected['cases'] if r['role']=='positive_task']
    boundaries=[r for r in corrected['cases'] if r['role']=='boundary']
    corrected.update(status='author_contract_review_of_saved_first_run',
        positive_tasks={'total':len(positives),'outcomes':dict(Counter(r['outcome'] for r in positives))},
        boundary_tasks={'total':len(boundaries),'outcomes':dict(Counter(r['outcome'] for r in boundaries))},
        groups={g:dict(Counter(r['outcome'] for r in corrected['cases'] if r['group']==g)) for g in dict.fromkeys(r['group'] for r in corrected['cases'])},
        score_adjustments=changes, independent_review=False, original_report_unchanged=True,
        product_changed=False,additional_http_requests=0,additional_model_calls=0,
        source_quality_flags=[{'id':'P17','note':'The field labelled English name contains botanical Latin-style names. Source-faithful display does not validate a reliable English common name.'}],
        original_summary_sha256=sha(batch/'summary.json'),raw_results_sha256=sha(batch/'raw_results.json'),
        reviewer_sha256=sha(Path(__file__)))
    output=batch/'review_01.json'
    with output.open('x') as f:
        import os
        os.chmod(output,0o600);json.dump(corrected,f,ensure_ascii=False,indent=2);f.write('\n')
    snapshot=batch/'reviewer_01.py';assert not snapshot.exists();shutil.copyfile(__file__,snapshot);snapshot.chmod(0o600)
    print(json.dumps({'status':corrected['status'],'positive_tasks':corrected['positive_tasks'],'boundary_tasks':corrected['boundary_tasks']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',type=Path,required=True)
    review(parser.parse_args().batch.resolve())
