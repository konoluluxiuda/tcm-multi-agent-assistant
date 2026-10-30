"""Large but entirely fabricated local catalog, shared by HTTP/browser checks."""
from contextlib import redirect_stdout
import io
import json
import os
from scripts.build_etcm_diseases import build
from tests.research_business_fixture import BusinessFixture
from tests.test_disease_catalog import document


class LargeBusinessFixture(BusinessFixture):
    def __enter__(self):
        super().__enter__()
        raw=document('Synthetic disease')
        ids=[f'P{i:05d}' for i in range(10000)]+[f'Q{i:05d}' for i in range(4000)]
        rows=[{'UniProt Accession Number':[value],'Gene Symbol':['SYNTHETIC'],
               'Synthetic note':'x'*1000 if i<20 else ''} for i,value in enumerate(ids)]
        raw['data'][1]['value'][0]['value']=rows
        raw['complete_fetch_summary'][0].update(actual=len(rows),expected=len(rows))
        (self.source/'etcm_diseases/Synthetic_disease.json').write_text(json.dumps(raw))
        with redirect_stdout(io.StringIO()):build(self.source,self.herbs.database,self.root/'large-diseases','large-synthetic-v1')
        self.diseases=self.root/'large-diseases/diseases.sqlite3'
        os.environ['ETCM_DISEASE_DB']=str(self.diseases)
        return self
