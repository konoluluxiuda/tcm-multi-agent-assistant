"""Run the production A graph against local synthetic business databases."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import socket
from tests.research_business_fixture import BusinessFixture
from tcm.disease_catalog import verify_witness
from tcm.research_run_local import run_local_actions
from tcm.runtime_versions import framework_versions


async def check():
    with BusinessFixture() as fixture:
        paths=(fixture.diseases,fixture.herbs.base,fixture.herbs.database)
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        result=await run_local_actions(fixture.diseases,fixture.scope(),
            herbs=fixture.herbs.base,enrichment=fixture.herbs.database)
        run=result['run']
        assert run['status']=='complete' and run['coverage']['complete'],result
        assert run['tool_calls']==6 and run['model_calls']==4
        profile_rows=ranking_rows=0
        for payload in run['evidence'].values():
            assert payload['status']!='error'
            data=payload['data']
            if payload['tool']=='disease_gene_profile':
                for row in data['rows']:
                    verify_witness(fixture.source,data['profile'],row);profile_rows+=1
            if payload['tool']=='rank_herbs_for_gene_set':
                assert [r['jaccard'] for r in data['items']]==[2/25,1/26]
                for item in data['items']:
                    herb=json.loads((fixture.root/'source'/item['source']['source']['relative_file_path']).read_text())
                    for pair in item['evidence']:
                        for row in pair['disease_rows']:
                            verify_witness(fixture.source,data['profile'],row);ranking_rows+=1
                        for row in pair['herb_rows']:
                            original=herb
                            for token in row['json_pointer'].split('/')[1:]:
                                original=original[int(token)] if isinstance(original,list) else original[token]
                            assert original==row['raw_row'];ranking_rows+=1
        assert profile_rows==24
        assert all(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p,digest in before.items())
        return {'status':'passed','data':'synthetic_only','external_model_calls':0,
                'driver':'deterministic_declared_actions_not_llm_planning',
                'framework':framework_versions(),'business_tools':5,'tool_calls':6,'driver_steps':4,
                'verified_profile_original_rows':profile_rows,'verified_ranking_witness_rows':ranking_rows,
                'source_databases_unchanged':True,'run':run,
                'limits':['Not a live-model quality or latency evaluation',
                          'The HTTP actions endpoint uses this local driver, not an external model',
                          'Free-text multi-requirement compilation and large-result offload are not integrated here']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    attempts=[]
    def denied(*args,**kwargs):
        attempts.append('blocked');raise RuntimeError('Network forbidden in synthetic A acceptance')
    with patch.object(socket.socket,'connect',denied),patch.object(socket.socket,'connect_ex',denied),\
         patch.object(socket.socket,'sendto',denied),patch.object(socket,'getaddrinfo',denied):
        report=asyncio.run(check())
    assert not attempts
    report['network_attempts']=len(attempts)
    root=Path(__file__).resolve().parents[1]
    sources=[Path(__file__).resolve(),root/'tests/research_business_fixture.py',
             root/'tests/test_research_run_business.py',*(root/'tcm').glob('*.py')]
    report['source_sha256']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    (args.out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','business_tools','tool_calls','driver_steps',
            'verified_profile_original_rows','verified_ranking_witness_rows','external_model_calls','network_attempts')}))


if __name__=='__main__':main()
