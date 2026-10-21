"""Local source reference leads and conservative metadata checks; no transport/model."""
from copy import deepcopy
import hashlib
import json
import os
import re

VERSION='task_literature_v1'


def reference_leads(task):
    from tcm.etcm_enrichment import relations
    from tcm.herbs import lookup
    from tcm.chat import DEFAULT_DATABASE
    groups=[]
    for record in task['selected_source_records']:
        data=relations(os.environ['ETCM_ENRICHMENT_DB'],record,task['snapshot']['dataset_version'],
                       kind='reference_pmid',limit=5)
        base=lookup(os.getenv('TCM_DB') or DEFAULT_DATABASE,source_record_id=record['source_record_id'],
                    dataset_version=task['snapshot']['dataset_version'])
        if (base['status']!='ok' or base['records'][0]['source']['file_sha256']!=record['source']['file_sha256']):
            raise ValueError('Reference source snapshot differs')
        field=base['records'][0]['fields']['reference_pmids']
        raw_source=field.get('source') or {}
        raw_values=raw_source.get('raw_value') or []
        candidates=[]
        for row in data['items']:
            pmid=row['identifier']
            anchors=[{'json_pointer':raw_source['json_pointer']+'/'+str(i),'source_value':value}
                     for i,value in enumerate(raw_values) if isinstance(value,str) and value.strip()==pmid]
            if not anchors:raise ValueError('Reference absent from original frozen field')
            candidates.append({'pmid':pmid,'valid_identifier':isinstance(pmid,str) and bool(re.fullmatch(r'[1-9][0-9]{0,9}',pmid)),
                'edge_id':row['edge_id'],'catalog_pointer':row['source_pointer'],
                'json_pointer':anchors[0]['json_pointer'],'source_value':anchors[0]['source_value'],
                'source_anchors':anchors,'normalized_row':deepcopy(row['raw_row']),
                'capture_status':deepcopy(row['capture_status']),
                'scope':'source_listed_reference_not_target_evidence'})
        groups.append({'source_record_id':record['source_record_id'],'source':deepcopy(record['source']),
            'total':data['total'],'returned_count':len(candidates),'truncated':data['total']>len(candidates),'candidates':candidates})
    return {'version':VERSION,'status':'local_ids_only','groups':groups,
        'metadata_fetched':False,'full_text_read':False,'target_relation_verified':False,
        'notice':'仅列出所选药材来源中的文献ID，每侧最多5条。未获取标题/摘要或核验文献是否讨论所选靶点；双方列出同一PMID也不证明共同机制。'}


def assess_candidate(article,*,pmid,source_id,leads,task_key,review=None):
    """Pure adapter contract. Exact ID/quote checks never approve botanical identity.

    Only metadata supplied by a future frozen local adapter can enter this function.
    Caller-provided annotations remain user claims even when their quotes match.
    """
    from tcm.literature_evidence import annotate_article
    from tcm.research_objects import scope_signals,validate_reviews
    if not isinstance(pmid,str) or not re.fullmatch(r'[1-9][0-9]{0,9}',pmid) or article.get('pmid')!=pmid:
        raise ValueError('Article PMID does not match the requested identifier')
    matches=[c for g in leads['groups'] if g['source_record_id']==source_id
             for c in g['candidates'] if c['valid_identifier'] and c['pmid']==pmid]
    if not matches:raise ValueError('Article is not listed by this selected source')
    annotated=annotate_article(article);flags=annotated['assessment']['flags']
    exclusions=sorted(set(flags)&{'retracted','retraction_notice','erratum_notice','expression_of_concern_notice'})
    notes=validate_reviews([article],[review] if review else [],task_key,task_key if review else None)
    payload=json.dumps(article,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return {'version':VERSION,'evidence_id':'LIT-'+hashlib.sha256(payload.encode()).hexdigest(),
        'pmid':pmid,'source_record_id':source_id,'source_reference_anchors':deepcopy(matches),
        'status':'excluded_source_notice' if exclusions else 'needs_object_review',
        'exclusion_flags':exclusions,'metadata_flags':flags,'object_scope_signals':scope_signals(article),
        'user_annotations':notes,'has_abstract':any(s.get('text','').strip() for s in article.get('abstract',[])),
        'object_identity_verified':False,'target_relation_verified':False,'direct_attribution_allowed':False,
        'quality_appraised':False,'full_text_read':False}
