"""Real local sources through /chat; no external models, no independent accuracy claim."""
import argparse
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unicodedata
from unittest.mock import patch

from scripts.run_offline_ci import ROOT, test_environment


def sha(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):result.update(chunk)
    return result.hexdigest()


def read_only(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)


def source_key(row):
    """Reference defined directly on original rows, not the serving compound index."""
    try:
        names=row['Ingredient Name'];formula=row['Molecular Formula'];mass=Decimal(str(row['Molecular Weight']))
        if len(names)!=1 or not isinstance(names[0],str) or not names[0].strip() or not isinstance(formula,str) or not formula.strip() or not mass.is_finite() or mass<=0:return None
        return unicodedata.normalize('NFKC',names[0]).strip().casefold(),formula.strip(),mass
    except (KeyError,TypeError,InvalidOperation):return None


def table_rows(document,kind):
    return [row for section in document.get('data',[]) if section.get('id')=='related_table'
            for table in section.get('value',[]) if table.get('type')==kind for row in table.get('value',[])]


class References:
    def __init__(self, source):
        self.source=Path(source);self.herbs={}
        for name in ('BaiZhu','HuangQi'):
            path=self.source/f'etcm_herbs/{name}.json';self.herbs[name]=json.loads(path.read_text())
        export=ROOT/'var/etcm_target_links_v1/target_ingredients.jsonl'
        self.target_counts=Counter();self.compounds=Counter()
        for line in export.open():
            row=json.loads(line);self.target_counts[row['target']['UniProt Accession Number']]+=1
            key=source_key(row['raw_row'])
            if key is not None:self.compounds[key]+=1
        with read_only(ROOT/'var/tcm/herbs.sqlite3') as con:
            self.herb_count=con.execute("SELECT COUNT(*) FROM records WHERE dataset_version='local-2026-10-10' AND state='valid'").fetchone()[0]
        with read_only(ROOT/'var/mineru_retrieval_v2/research.sqlite3') as con:
            self.entry_count=con.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
            self.chunks={row[0]:dict(zip(('name','label','raw_text'),row[1:])) for row in con.execute('SELECT chunk_id,name,label,raw_text FROM chunks')}

    def expected(self,case):
        kind=case['kind']
        if kind=='ingredient':return len(table_rows(self.herbs[case['herbs'][0]],'ingredient'))
        if kind=='common_target':
            sets=[{identifier for row in table_rows(self.herbs[n],'target') for identifier in row.get('UniProt Accession Number',[])} for n in case['herbs']]
            return len(sets[0]&sets[1])
        if kind=='target_ingredient':return self.target_counts[case['uniprot']]
        if kind=='herb_target_paths':return sum(self.compounds.get(source_key(row),0) for row in table_rows(self.herbs[case['herbs'][0]],'ingredient'))
        return None

    def pointer(self,relative,pointer,value,expected_sha=None):
        path=(self.source/relative).resolve()
        assert path.is_relative_to(self.source.resolve())
        assert path.stat().st_size<=96*1024*1024,'Source too large for this sample checker'
        if expected_sha:assert sha(path)==expected_sha,'Source SHA differs'
        original=json.loads(path.read_text())
        for token in pointer.split('/')[1:]:
            token=token.replace('~1','/').replace('~0','~')
            original=original[int(token)] if isinstance(original,list) else original[token]
        assert original==value,'Source pointer does not reproduce the displayed row'

    def hits(self,hits,name,field):
        assert hits,'No evidence hits'
        for hit in hits:
            original=self.chunks[hit['chunk_id']]
            assert hit['name']==original['name']==name
            assert hit['label']==original['label']==field
            assert hit['raw_text']==original['raw_text']
            assert hit.get('evidence'),'Missing page provenance'


def assess(case,http,body,refs):
    if http==403:return {'outcome':'unsupported_intent','reason':'Rule-only entry did not recognize this wording; external model fallback was disabled.'}
    if http>=400:return {'outcome':'technical_failure','reason':f'HTTP {http}'}
    try:
        kind=case['kind']
        if kind=='overview':
            result=body['assistant'];assert result['topic']=='overview'
            assert result['herbs']['source_record_count']==refs.herb_count
            assert result['pharmacopoeia']['entry_count']==refs.entry_count
        elif kind=='pharmacopoeia':
            result=body['research'];refs.hits(result['tool_result']['hits'],case['name'],case['field'])
            if result['status']!='candidates':return {'outcome':'partial','reason':result['status']}
        elif kind=='comparison':
            result=body['research'];comparison=result['tool_result']['comparison']
            assert comparison['names']==case['names'] and comparison['fields']==case['fields']
            assert [g['name'] for g in comparison['groups']]==case['names']
            complete=True
            for group in comparison['groups']:
                assert [c['field'] for c in group['cells']]==case['fields']
                for cell in group['cells']:
                    if cell['status']=='candidates':refs.hits(cell['result']['hits'],group['name'],cell['field'])
                    else:complete=False
            if not complete:return {'outcome':'partial','reason':'At least one comparison field was missing, ambiguous or unresolved.'}
        elif kind=='herb_field':
            result=body['tcm'];assert result['status']=='ok'
            field=result['records'][0]['fields'][case['field']]
            original=[item['value'] for s in refs.herbs[case['herb']]['data'] if s.get('id')=='base_information' for item in s.get('value',[]) if item.get('key')==case['raw_key']]
            assert original==[field['value']]
            refs.pointer(field['source']['relative_file_path'],field['source']['json_pointer'],field['source']['raw_value'],field['source']['file_sha256'])
        elif kind in ('not_covered','unknown_herb'):
            result=body['assistant'];assert result['status']=='clarification_needed' and 'data' not in result['relations']
            if kind=='not_covered':assert result['relations']['resolution_status']=='not_covered'
            else:assert result['relations']['groups'] and all(not group['options'] for group in result['relations']['groups'])
            return {'outcome':'expected_clarification','reason':'No fabricated source relationship.'}
        else:
            payload=body['assistant']['relations'];assert payload['kind']==kind
            data=payload['data'];assert data['total']==refs.expected(case),'Count differs from independent source-row calculation'
            assert data['items'],'No first-page evidence'
            item=data['items'][0]
            if kind=='ingredient':
                source=payload['groups'][0]['options'][0]['source']
                refs.pointer(source['relative_file_path'],item['source_pointer'],item['raw_row'],source['file_sha256'])
            elif kind=='common_target':
                for index,source_rows in enumerate(item['sources']):
                    source=payload['groups'][index]['options'][0]['source'];w=source_rows['witnesses'][0]
                    refs.pointer(source['relative_file_path'],w['source_pointer'],w['raw_row'],source['file_sha256'])
                    assert item['identifier'] in w['raw_row']['UniProt Accession Number']
            elif kind=='target_ingredient':
                assert payload['target']['uniprot']==case['uniprot']
                refs.pointer(item['source_file'],item['json_pointer'],item['raw_row'],item['source_file_sha256'])
            elif kind=='herb_target_paths':
                h,t=item['herb_evidence'],item['target_evidence']
                assert source_key(h['raw_row'])==source_key(t['raw_row']) and source_key(h['raw_row']) is not None
                assert item['compound_identity_verified'] is False
                refs.pointer(h['source']['relative_file_path'],h['json_pointer'],h['raw_row'],h['source']['file_sha256'])
                refs.pointer(t['source_file'],t['json_pointer'],t['raw_row'],t['source_file_sha256'])
        return {'outcome':'completed_local_scope','reason':'Task checks passed against the frozen local source scope; no clinical or semantic validation.'}
    except (AssertionError,KeyError,TypeError,ValueError,IndexError,OSError) as error:
        return {'outcome':'source_or_contract_failure','reason':str(error) or type(error).__name__}


def worker(args):
    import os,dotenv
    suite=json.loads(args.suite.read_text());out=args.out
    refs=References(args.source)
    (out/'references.json').write_text(json.dumps({'counts':{c['id']:refs.expected(c) for c in suite['cases']},
                'basis':'Original herb rows and prior audited target export, computed before HTTP requests; not independent medical annotation.'},ensure_ascii=False,indent=2))
    files=['app.py','config.py','features.py',str(args.suite.relative_to(ROOT))]
    files += [str(p.relative_to(ROOT)) for folder in ('tcm','agents','scripts') for p in (ROOT/folder).rglob('*.py')]
    files += ['templates/index.html']
    hashes={}
    for name in sorted(set(files)):
        path=ROOT/name;hashes[name]=sha(path);destination=out/'source'/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
    data_paths=['var/tcm/herbs.sqlite3','var/etcm_enrichment_v1/data/enrichment.sqlite3','var/etcm_target_links_v1/targets-paths.sqlite3',
                'var/etcm_target_links_v1/report.json','var/etcm_target_links_v1/target_ingredients.jsonl','var/mineru_retrieval_v2/research.sqlite3']
    data_hashes={name:sha(ROOT/name) for name in data_paths}
    report={'suite_version':suite['version'],'suite_sha256':sha(args.suite),'independent_acceptance':False,'human_reviewed':False,
            'scope':'Post-development local deterministic tools via FastAPI ASGI, external AI disabled; no LLM quality score.',
            'source_code_sha256':hashes,'data_sha256':data_hashes,'cases':[]}
    network,models=[],[]
    def deny(*a,**kw):network.append('blocked');raise RuntimeError('External network forbidden')
    def no_model(*a,**kw):models.append('blocked');raise RuntimeError('Model invocation forbidden')
    with ExitStack() as stack:
        stack.enter_context(patch.object(dotenv,'load_dotenv',return_value=False))
        for obj,name in [(socket.socket,'connect'),(socket.socket,'connect_ex'),(socket.socket,'sendto'),(socket,'getaddrinfo')]:stack.enter_context(patch.object(obj,name,deny))
        from scripts.preview_evidence import preview_environment
        env=preview_environment(out/'state',local_research=True,local_herbs=True)
        env.update(ENABLE_EVIDENCE_AUTO_ROUTING='0',ETCM_SOURCE_ROOT=str(args.source),
                   ETCM_ENRICHMENT_DB=str(ROOT/'var/etcm_enrichment_v1/data/enrichment.sqlite3'),
                   ETCM_TARGET_DB=str(ROOT/'var/etcm_target_links_v1/targets-paths.sqlite3'))
        stack.enter_context(patch.dict(os.environ,env))
        from fastapi.testclient import TestClient
        from langchain_core.runnables import RunnableLambda
        from agents import agent_decision as ad
        from agents.conversation_store import ConversationStore
        import app
        stack.enter_context(patch.object(ad,'memory',ConversationStore(out/'state/memory.sqlite3')))
        for config in (ad.config.agent_decision,ad.config.conversation):stack.enter_context(patch.object(config,'llm',RunnableLambda(no_model)))
        for name in ('check_input','check_output'):stack.enter_context(patch.object(ad.LocalGuardrails,name,no_model))
        for case in suite['cases']:
            started=time.perf_counter();turns=[]
            with TestClient(app.app,raise_server_exceptions=False) as client:
                def send(query):
                    before=time.perf_counter()
                    with redirect_stdout(io.StringIO()):response=client.post('/chat',json={'query':query,'answer_mode':'auto'})
                    try:body=response.json()
                    except ValueError:body={'non_json_error':response.text[:500]}
                    turns.append({'query':query,'http_status':response.status_code,'latency_ms':round((time.perf_counter()-before)*1000,3),'body':body})
                    return response.status_code,body
                def select(http,body):
                    payload=body.get('assistant',{}).get('relations',{})
                    if case.get('selection') and 'data' not in payload and payload.get('groups'):
                        ids=[]
                        for group,name in zip(payload['groups'],case['herbs']):
                            options=[o for o in group['options'] if o['source']['relative_file_path']==f'etcm_herbs/{name}.json']
                            if len(options)!=1:return http,body
                            ids.append(options[0]['source_record_id'])
                        label={'ingredient':'成分','common_target':'共同靶点','herb_target_paths':'成分靶点'}[payload['kind']]
                        return send('/relations '+label+' '+' | '.join(ids))
                    return http,body
                if case.get('setup_query'):select(*send(case['setup_query']))
                http,body=select(*send(case['query']))
                assessment=assess(case,http,body,refs)
            row={'id':case['id'],'origin':case['origin'],'kind':case['kind'],'query':case['query'],**assessment,
                 'elapsed_ms':round((time.perf_counter()-started)*1000,3),'http_calls':len(turns),
                 'simulated_source_selection':any(t['query'].startswith('/relations ') for t in turns),'turns':turns}
            report['cases'].append(row)
            (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({k:v for k,v in row.items() if k not in ('turns',)},ensure_ascii=False),flush=True)
    report.update(outcome_counts=dict(Counter(c['outcome'] for c in report['cases'])),model_attempts=len(models),network_attempts=len(network),
                  run_status='completed',all_tasks_completed=all(c['outcome']=='completed_local_scope' for c in report['cases']),
                  external_model_cost_usd=0 if not models and not network else None,local_compute_cost_usd=None,
                  latency_note='One local run including simulated choices, no user think time; not a production P95.',
                  database_unchanged=sha(ROOT/'var/tcm/herbs.sqlite3')==data_hashes['var/tcm/herbs.sqlite3'])
    (out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 1 if models or network or not report['database_unchanged'] else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',type=Path,default=ROOT/'eval/local_usability_v1.json')
    parser.add_argument('--source',type=Path,default=Path('/home/zry/workspace/HDCTI/dataset/ETCM2.0'))
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args();args.suite=args.suite.resolve();args.out=args.out.resolve();args.source=args.source.resolve()
    if args.worker:return worker(args)
    if args.out.is_relative_to(args.source):raise ValueError('Output must be outside original snapshot')
    args.out.mkdir(parents=True,exist_ok=False,mode=0o700)
    return subprocess.call([sys.executable,'-m','scripts.eval_local_usability','--worker','--suite',str(args.suite),'--source',str(args.source),'--out',str(args.out)],
                           cwd=ROOT,env=test_environment())


if __name__=='__main__':sys.exit(main())
