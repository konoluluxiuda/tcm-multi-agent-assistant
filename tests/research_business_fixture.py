"""Synthetic files used by the production A-loop acceptance checks."""
from contextlib import redirect_stdout
import io
import json
import os
from unittest.mock import patch
from scripts.build_etcm_diseases import build
from tests import test_relation_queries as herbs
from tests.test_disease_catalog import document
from tcm.herbs import lookup
from tcm.research_run_scope import RunScope


class BusinessFixture:
    def __enter__(self):
        self.herbs=herbs.RelationQueryTests();self.herbs.setUp()
        self.root=self.herbs.root
        self.source=self.root/'disease-source';directory=self.source/'etcm_diseases';directory.mkdir(parents=True)
        raw=document('Synthetic disease')
        rows=[{'UniProt Accession Number':[v],'Gene Symbol':['SYNTHETIC']} for v in ('P00000','Q00000','R99999')]*8
        raw['data'][1]['value'][0]['value']=rows
        raw['complete_fetch_summary'][0].update(actual=len(rows),expected=len(rows))
        (directory/'Synthetic_disease.json').write_text(json.dumps(raw))
        with redirect_stdout(io.StringIO()):build(self.source,self.herbs.database,self.root/'diseases','disease-v1')
        self.diseases=self.root/'diseases/diseases.sqlite3'
        self.env=patch.dict(os.environ,{'ETCM_DISEASE_DB':str(self.diseases)})
        self.env.start()
        self.herb_id=lookup(self.herbs.base,name='Alpha',dataset_version='v1')['records'][0]['source_record_id']
        return self

    def __exit__(self,*args):
        self.env.stop();self.herbs.doCleanups()

    def scope(self):
        return RunScope.model_validate({'requirements':[
            {'id':'discover','call':{'tool':'search_disease_sources','arguments':{'name':'Synthetic disease'}}},
            {'id':'original-rows','source_from':'discover','all_pages':True,
             'call':{'tool':'disease_gene_profile','arguments':{}}},
            {'id':'candidates','source_from':'discover',
             'call':{'tool':'rank_herbs_for_gene_set','arguments':{'top_k':5}}},
            {'id':'herb-profile','call':{'tool':'herb_target_profile','arguments':{'name':self.herb_id}}},
            {'id':'herb-neighbours','call':{'tool':'herb_target_similarity','arguments':{'name':self.herb_id,'top_k':5}}},
        ]})
