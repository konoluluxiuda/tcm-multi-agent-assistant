"""Deterministic large observations shared by evidence-offload checks; no providers."""
MARKER='SYNTHETIC_MARKER_MIDDLE_017'


def fixture(count):
    ids=[f'SYNTHETIC_ID_{i:06d}' for i in range(count)]
    ids[count//2]=MARKER
    return {'tool':'disease_gene_profile','status':'ok','elapsed_ms':0.0,'data':{
        'record_id':'etcm-disease:'+'a'*64,'dataset_version':'context-synthetic-v1',
        'catalog_revision':'synthetic','profile':{'name':'Synthetic disease','target_count':count,
        'source':{'file_sha256':'a'*64}},'target_ids':ids,'rows':[],
        'page':1,'has_next':False,'identical_set_sources':1}}

