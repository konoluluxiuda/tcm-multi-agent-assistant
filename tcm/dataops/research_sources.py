"""Version-bound ETCM + document access; same sources for F/P/A."""
from pathlib import Path
from tcm.rag_benchmark import read
from tcm.rag_retrieval import ReleaseSearch
from tcm.mineru_guards import field_body,reference_target
from tcm.dataops.data_access import Sources,SourceChanged,token


class ReleaseSources(Sources):
    def __init__(self,release):
        self.release=Path(release).resolve();self.release_token=token(self.release/'release.json')
        config=read(self.release/'sources.private.json')['config']
        super().__init__(config['herbs'],config['enrichment'],self.release/'index')

    def __enter__(self):
        self.search=ReleaseSearch(self.release)
        try:
            super().__enter__()
            self.revision.update(release_content_id=self.search.release['content_id'],rules='dataops-research-v1')
            return self
        except BaseException:
            self.search.close();raise

    def __exit__(self,*args):
        try:super().__exit__(*args)
        finally:self.search.close()

    def assert_unchanged(self):
        super().assert_unchanged()
        if self.release_token!=token(self.release/'release.json'):raise SourceChanged('Release changed')

    def inspect(self,record,fields,*,candidate_id=None):
        result=super().inspect(record,fields,candidate_id=candidate_id)
        if any((record['source_record_id'],c['entry_id']) in self.search.quarantine for c in result['candidates']):
            return {'status':'source_mismatch','candidates':result['candidates'],'fields':{},'comparison':None,
                    'reason':'quarantined_name_candidate','identity_verified':False}
        for cell in result['fields'].values():
            if cell['value'] and (not field_body(cell['value']) or reference_target(cell['value'])):
                cell.update(status='reference_unresolved',value=None)
        return result
