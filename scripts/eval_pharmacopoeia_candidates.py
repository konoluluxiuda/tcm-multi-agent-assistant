"""Read-only checks of actual extracted candidates against the prior OCR references."""
import argparse
import json
from pathlib import Path
import unicodedata
from tcm.pharmacopoeia import digest, query_candidates, MISSING

ROOT=Path(__file__).resolve().parents[1]
MAP={'latin_drug_name':'latin_drug_name','medicinal_part_fragment':'medicinal_part',
     'properties_meridians':'properties_meridians','quantity_fragment':'usage','caution_fragment':'cautions'}


def norm(value):return ''.join(unicodedata.normalize('NFKC',value).split())


def evaluate(data,suite):
    checks=[]
    for case in suite['cases']:
        for target,expected in case['expected_fragments'].items():
            if target not in MAP:continue
            result=query_candidates(data,case['id'],MAP[target]);field=result['result']
            occurrences=field['occurrences']
            matched=[o for o in occurrences if norm(expected) in norm(o['raw_text'])]
            valid=bool(matched) and result['status']=='unreviewed_candidate' and result['human_signoff'] is False
            for occurrence in matched:
                valid=valid and bool(occurrence['evidence']) and bool(occurrence.get('previews'))
                for line in occurrence['evidence']:
                    source=suite['documents'][line['document']]
                    valid=valid and line['source_sha256']==source['sha256'] and line['printed_page']==source['printed_pages'][str(line['pdf_page'])]
            checks.append({'case':case['id'],'field':MAP[target],'check':'prior_reference_fragment_and_provenance','passed':bool(valid)})
    for name in ('YiZhiHuangHua','DaFuPi','ShanMaiDong'):
        result=query_candidates(data,name,'cautions')['result']
        checks.append({'case':name,'field':'cautions','check':'missing_is_not_negative_clinical_fact',
                       'passed':result['state']==MISSING and result['occurrences']==[] and '不表示无禁忌' in result['message']})
    raw=query_candidates(data,'DaFuPi','pinyin')
    checks.append({'case':'DaFuPi','check':'bad_pinyin_quarantined_not_corrected',
        'passed':raw['review_status']=='quarantined' and raw['result']['occurrences'][0]['raw_text']=='dnpa'})
    raw=query_candidates(data,'ShanMaiDong','origin')
    checks.append({'case':'ShanMaiDong','check':'bad_taxon_quarantined_not_corrected',
        'passed':raw['review_status']=='quarantined' and 'spicala' in raw['result']['occurrences'][0]['raw_text']})
    effect=query_candidates(data,'ShanMaiDong','efficacy_indications')['result']['occurrences'][0]
    checks.append({'case':'ShanMaiDong','check':'cross_column_field_has_both_evidence_columns',
                   'passed':{e['column'] for e in effect['evidence']}=={'left','right'}})
    caution=query_candidates(data,'DingGongTeng','cautions')['result']['occurrences'][0]
    checks.append({'case':'DingGongTeng','check':'cross_page_entry_caution_is_from_page_four',
                   'passed':{e['printed_page'] for e in caution['evidence']}=={4}})
    checks.append({'case':'unknown','check':'no_implicit_entity','passed':query_candidates(data,'未收录','origin')['status']=='not_found'})
    return checks


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,default=ROOT/'var/pharmacopoeia_candidates/candidates.json')
    p.add_argument('--out',type=Path,default=ROOT/'var/pharmacopoeia_candidates/checks.json')
    a=p.parse_args();data=json.loads(a.data.read_text());suite_path=ROOT/'eval/pharmacopoeia_ocr_sample_v1.json'
    checks=evaluate(data,json.loads(suite_path.read_text()))
    report={'status':'passed' if all(c['passed'] for c in checks) else 'failed','checks':checks,
            'total':len(checks),'passed':sum(c['passed'] for c in checks),'human_signoff':False,
            'business_task_completion_rate':None,'clinical_truth_verified':False,
            'candidates_sha256':digest(a.data),'reference_sha256':digest(suite_path),
            'code_sha256':{s:digest(ROOT/s) for s in ['tcm/pharmacopoeia.py','scripts/eval_pharmacopoeia_candidates.py']}}
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','total','passed')}))
    raise SystemExit(0 if report['status']=='passed' else 1)
