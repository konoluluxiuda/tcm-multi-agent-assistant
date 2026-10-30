"""Cross-source synthetic fixture; includes real index building and local workers."""
import json
import os
from unittest.mock import patch
from tests.research_business_fixture import BusinessFixture
from tests.test_mineru_retrieval import fixture
from scripts.build_mineru_retrieval import build
from tcm.mineru_pharmacopoeia import digest
from tcm.herbs import lookup
from tcm.research_plan_intent import local_plan
from tcm.research_plan_run import compile_scope

QUESTION='比较Alpha与Beta的药典功效和用量，核对全部共同靶点，列出双方的ETCM药用部位，保留双方原始记录的位置'


class ReportFixture(BusinessFixture):
    def __enter__(self):
        super().__enter__()
        source=self.root/'pharma-source';fixture(source)
        path=source/'research_view.json';raw=json.loads(path.read_text())
        for entry,name in zip(raw['entries'],('Alpha','Beta')):
            entry.update(name_zh_candidate=name,raw_name_zh_candidate=name)
            entry['fields']=entry['fields'][:2]
            for item in entry['fields']:
                item['text']=item['raw_text']='【'+item['label']+'】SYNTHETIC_'+name+'_'+item['label']
        path.write_text(json.dumps(raw,ensure_ascii=False))
        summary=source/'summary.json';data=json.loads(summary.read_text());data['research_view_sha256']=digest(path);summary.write_text(json.dumps(data))
        self.pharma=self.root/'pharma-index';build(source,self.pharma)
        self.report_env=patch.dict(os.environ,{'PHARMACOPOEIA_INDEX':str(self.pharma),'ENABLE_PHARMACOPOEIA_RESEARCH':'1',
            'LOCAL_DENSE_MODEL':'','LOCAL_DENSE_CACHE':'','LOCAL_RERANKER_MODEL':'','LOCAL_DENSE_PYTHON':''})
        self.report_env.start()
        self.ids=[lookup(self.herbs.base,name=n,dataset_version='v1')['records'][0]['source_record_id'] for n in ('Alpha','Beta')]
        return self

    def __exit__(self,*args):
        self.report_env.stop();super().__exit__(*args)

    def report_scope(self):return compile_scope(local_plan(QUESTION),{})[0]
