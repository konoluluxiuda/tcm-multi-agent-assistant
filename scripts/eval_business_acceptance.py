"""Freeze and run an author-designed batch against the fixed local rule-only container.

No application changes, question rewriting, provider calls or automatic retries.
Raw evidence and session responses stay in an exclusive private output directory.
"""
import argparse
from collections import Counter
import hashlib
import http.cookiejar as cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from scripts.eval_local_usability import References, source_key, table_rows
from scripts.run_offline_ci import ROOT

URL = 'http://127.0.0.1:7876'
CONTAINER = 'tcm-research-candidate-chat-1'
BUILD = ROOT/'var/multi_herb_comparison_v1/build.json'
VIEW = ROOT/'var/mineru_revisions_v2/research_view.json'
SOURCE = Path('/home/zry/workspace/HDCTI/dataset/ETCM2.0')
SELF = Path(__file__).resolve()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):h.update(block)
    return h.hexdigest()


def write(path, data):
    with Path(path).open('w', encoding='utf-8') as f:
        os.chmod(path, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def runtime():
    c = json.loads(subprocess.check_output(['docker', 'inspect', CONTAINER], text=True))[0]
    env = dict(item.split('=', 1) for item in c['Config']['Env'])
    assert env.get('RESEARCH_TOOLS_ONLY') == '1', 'Not a rule-only deployment'
    for key in ('ENABLE_PUBMED', 'ENABLE_EVIDENCE_ASSISTANT', 'ENABLE_EVIDENCE_AUTO_ROUTING'):
        assert env.get(key, '0').lower() in ('0', 'false', 'off', ''), 'External tool or AI enabled'
    assert c['State']['Status'] == 'running' and c['State']['Health']['Status'] == 'healthy'
    return {'image_id': c['Image'], 'image_tag': c['Config']['Image'], 'container_id': c['Id'],
            'rule_only': True, 'pubmed_enabled': False, 'ai_enabled': False}


def validate_suite(suite):
    assert suite['independent_acceptance'] is False
    cases = suite['cases']
    assert len(cases) == 24 and len({c['id'] for c in cases}) == 24
    assert all(c['origin'] == 'author_designed_after_development' for c in cases)
    for c in cases:
        assert c['role'] in ('positive_task', 'boundary') and 1 <= len(c['query']) <= 500
        assert c['kind'] in ('pharmacopoeia', 'comparison', 'herb_field', 'target_ingredient',
                             'disease', 'herb_target_paths', 'boundary')


def prepare(suite_path, out):
    suite = json.loads(suite_path.read_text());validate_suite(suite)
    build = json.loads(BUILD.read_text());actual = runtime()
    assert actual['image_id'] == build['image_id'], 'Unexpected current image'
    for name, fingerprint in build['source_files_sha256'].items():
        assert sha(ROOT/name) == fingerprint, 'Runtime source differs from built image: '+name
    # No serving query is made here. References come from original view/source rows.
    original = json.loads(VIEW.read_text())
    required_names = {name for c in suite['cases'] for name in c.get('names', [c.get('name')]) if name}
    entries = [e for e in original['entries'] if e['name_zh_candidate'] in required_names]
    assert {e['name_zh_candidate'] for e in entries} == required_names
    refs = References(SOURCE)
    counts = {};herb_fields = {}
    for c in suite['cases']:
        if c['kind'] == 'herb_field':
            values = [r['value'] for s in refs.herbs[c['herb']]['data'] if s.get('id') == 'base_information'
                      for r in s.get('value', []) if r.get('key') == c['raw_key']]
            assert len(values) == 1
            herb_fields[c['id']] = values[0]
        if c['kind'] == 'disease':counts[c['id']] = len(table_rows(refs.herbs[c['herb']], 'disease'))
        if c['kind'] == 'herb_target_paths':
            counts[c['id']] = refs.expected({'kind': c['kind'], 'herbs': [c['herb']]})
        if c['kind'] == 'target_ingredient':counts[c['id']] = refs.target_counts[c['uniprot']]
    reference = {'entries': entries, 'counts': counts, 'herb_fields': herb_fields}
    for c in suite['cases']:
        if c['kind'] == 'pharmacopoeia':
            labels = {f['label'] for e in entries if e['name_zh_candidate'] == c['name'] for f in e['fields']}
            assert set(c.get('expected_missing', [])) == set(c['fields'])-labels
    # Exact-text deduplication is disclosed; it cannot establish semantic independence.
    previous = set()
    for file in (ROOT/'eval').glob('*.json'):
        if file.resolve() == suite_path.resolve():continue
        try:data = json.loads(file.read_text())
        except (ValueError, OSError):continue
        def collect(value):
            if isinstance(value, dict):
                for k,v in value.items():
                    if k in ('query', 'question', 'setup_query') and isinstance(v, str):previous.add(v)
                    else:collect(v)
            elif isinstance(value, list):
                for v in value:collect(v)
        collect(data)
    matches = [{'id': c['id'], 'query': c['query']} for c in suite['cases'] if c['query'] in previous]
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    write(out/'suite.json', suite);write(out/'references.json', reference)
    copy = out/'runtime_source';copy.mkdir(mode=0o700)
    for name in build['source_files_sha256']:
        dest = copy/name;dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT/name, dest);os.chmod(dest, 0o600)
    shutil.copyfile(SELF, out/'evaluator.py');os.chmod(out/'evaluator.py', 0o600)
    inputs = {str(p):sha(p) for p in (VIEW, ROOT/'var/mineru_retrieval_v2/research.sqlite3',
              ROOT/'var/tcm/herbs.sqlite3', ROOT/'var/etcm_target_links_v1/target_ingredients.jsonl',
              SOURCE/'etcm_herbs/HuangQi.json', SOURCE/'etcm_herbs/BaiZhu.json')}
    frozen = {'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'scope': 'prospective_author_designed_screening_not_independent_acceptance',
              'runtime': actual, 'source_files_sha256': build['source_files_sha256'],
              'suite_sha256': sha(out/'suite.json'), 'references_sha256': sha(out/'references.json'),
              'evaluator_sha256': sha(SELF), 'input_sha256': inputs,
              'exact_question_matches_in_prior_eval': matches,
              'note': 'Local pre-run freeze, not independent custody or a signed timestamp. Similar known templates recur.'}
    write(out/'frozen.json', frozen)
    print(json.dumps({'status':'prepared','cases':len(suite['cases']),'exact_prior_matches':len(matches),'out':str(out)}))


def verify_frozen(out):
    frozen = json.loads((out/'frozen.json').read_text())
    assert runtime() == frozen['runtime'], 'Container changed since freeze'
    assert sha(SELF) == frozen['evaluator_sha256'] == sha(out/'evaluator.py')
    assert sha(out/'suite.json') == frozen['suite_sha256']
    assert sha(out/'references.json') == frozen['references_sha256']
    for name, fingerprint in frozen['source_files_sha256'].items():
        assert sha(ROOT/name) == fingerprint == sha(out/'runtime_source'/name)
    for path, fingerprint in frozen['input_sha256'].items():assert sha(path) == fingerprint
    return frozen


class SourceChecks:
    def __init__(self, reference):
        self.reference = reference
        self.entries = {e['id']: e for e in reference['entries']}
        self.originals = {}

    def hits(self, hits, name, fields):
        assert hits, 'No evidence returned'
        labels = set()
        for h in hits:
            e = self.entries[h['entry_id']];f = e['fields'][h['field_index']]
            assert h['name'] == e['name_zh_candidate'] == name, 'Different herb evidence'
            assert h['label'] == f['label'] and h['label'] in fields, 'Different field evidence'
            assert h['raw_text'] == f['raw_text'] and h['text'] == f['text'], 'Source text differs'
            assert h['evidence'] == f['evidence'] and h['evidence'], 'Source anchors differ'
            labels.add(h['label'])
        return labels

    def pointer(self, relative, pointer, value, fingerprint):
        path = (SOURCE/relative).resolve()
        assert path.is_relative_to(SOURCE.resolve()) and path.stat().st_size <= 96*1024*1024
        if path not in self.originals:
            assert sha(path) == fingerprint
            self.originals[path] = (fingerprint, json.loads(path.read_text()))
        assert self.originals[path][0] == fingerprint
        original = self.originals[path][1]
        for token in pointer.split('/')[1:]:
            token = token.replace('~1', '/').replace('~0', '~')
            original = original[int(token)] if isinstance(original, list) else original[token]
        assert original == value, 'JSON source row differs'


def assess(case, http, body, refs, download=None):
    """Positive tasks and boundary behavior have separate numerators."""
    def result(outcome, reason):return {'outcome': outcome, 'reason': reason}
    if case['kind'] == 'boundary' and case['expected'] == 'pubmed_disabled':
        if http == 503 and body.get('pubmed', {}).get('status') == 'pubmed_disabled':
            return result('tool_unavailable', 'Deployment explicitly disables PubMed; original retrieval task was not completed.')
    if http == 403:return result('unsupported_intent', 'Generic unsupported entry; original question was not rewritten.')
    if http >= 400:return result('technical_failure', 'HTTP '+str(http))
    try:
        kind = case['kind']
        if kind == 'boundary':
            research = body.get('research', {});assistant = body.get('assistant', {})
            expected = case['expected']
            if expected == 'personal_medication_boundary':
                assert assistant.get('topic') == expected and assistant.get('status') == 'clarification_needed'
                assert not body.get('research') and not body.get('tcm') and not assistant.get('relations', {}).get('data')
                return result('appropriate_refusal', 'Personal medication boundary, not task completion.')
            if expected == 'cross_source_clarification':
                relations = assistant.get('relations', {})
                assert not relations.get('data') and not body.get('tcm', {}).get('records')
                assert (assistant.get('status') == 'clarification_needed' and
                        assistant.get('topic') in ('research_name_needed','relation_source_needed','clarify_sources','etcm_relations')) or research.get('status') == 'clarify_entity'
            else:
                assert research.get('status') == expected, 'Expected specific boundary: '+expected
                hits = (research.get('tool_result') or {}).get('hits', [])
                if expected == 'ambiguous_field':refs.hits(hits, case['name'], case['fields'])
                else:assert not hits, 'Unexpected facts for unresolved entity'
            return result('appropriate_clarification', 'Specific unresolved scope/identity; no fabricated completion.')
        if kind in ('pharmacopoeia', 'comparison'):
            research = body.get('research', {})
            if research.get('status') in ('clarify_comparison','clarify_entity','field_not_resolved','name_not_resolved','variant_scope_unresolved','ambiguous_field','ambiguous_name'):
                return result('clarification', 'Positive task not completed: '+research['status'])
            if not research and body.get('assistant', {}).get('status') == 'clarification_needed':
                return result('clarification', 'Assistant requested clarification instead of completing source query.')
            data = research.get('tool_result') or {}
            if kind == 'pharmacopoeia':
                labels = refs.hits(data.get('hits', []), case['name'], case['fields'])
                if case.get('require_followup'):assert research.get('followup') is True
                if set(case['fields']) != labels or research.get('status') != 'candidates':
                    return result('partial', 'Requested fields missing, ambiguous or incomplete; full completion not credited.')
            else:
                comp = data['comparison'];assert comp['names'] == case['names'] and comp['fields'] == case['fields']
                assert [g['name'] for g in comp['groups']] == case['names']
                complete = research['status'] == 'candidates'
                for group in comp['groups']:
                    assert [c['field'] for c in group['cells']] == case['fields']
                    for cell in group['cells']:
                        if cell['status'] != 'candidates':complete = False
                        else:refs.hits(cell['result']['hits'], group['name'], [cell['field']])
                if not complete:return result('partial', 'Comparison includes incomplete cells.')
                if case.get('export'):
                    assert download and download['http_status'] == 200 and download['attachment']
                    text = download['text']
                    assert text.count('## 药材：') == len(case['names'])
                    assert text.count('### 请求字段：') == len(case['names'])*len(case['fields'])
                    for g in comp['groups']:
                        assert '## 药材：'+g['name'] in text
                        for c in g['cells']:
                            for h in c['result']['hits']:
                                assert h['text'] in text and h['raw_text'] in text
        elif kind == 'herb_field':
            data = body['tcm'];assert data['status'] == 'ok' and len(data['records']) == 1
            field = data['records'][0]['fields'][case['field']]
            assert field['value'] == refs.reference['herb_fields'][case['id']]
            src = field['source']
            assert src['relative_file_path'] == 'etcm_herbs/'+case['herb']+'.json'
            refs.pointer(src['relative_file_path'], src['json_pointer'], src['raw_value'], src['file_sha256'])
        else:
            assistant = body['assistant'];payload = assistant['relations']
            if 'data' not in payload:
                return result('clarification', 'Relation request requires scope/source clarification.')
            assert payload['kind'] == kind
            data = payload['data'];assert data['total'] == refs.reference['counts'][case['id']]
            assert data['items']
            if kind == 'target_ingredient':
                assert payload['target']['uniprot'] == case['uniprot']
                assert payload['page'] == case['page'] and data['offset'] == (case['page']-1)*20
                for row in data['items']:refs.pointer(row['source_file'],row['json_pointer'],row['raw_row'],row['source_file_sha256'])
            elif kind == 'disease':
                src = payload['groups'][0]['options'][0]['source']
                assert src['relative_file_path'] == 'etcm_herbs/'+case['herb']+'.json'
                for row in data['items']:refs.pointer(src['relative_file_path'],row['source_pointer'],row['raw_row'],src['file_sha256'])
            elif kind == 'herb_target_paths':
                for row in data['items']:
                    h,t = row['herb_evidence'],row['target_evidence']
                    assert source_key(h['raw_row']) == source_key(t['raw_row']) is not None
                    assert row['compound_identity_verified'] is False
                    refs.pointer(h['source']['relative_file_path'],h['json_pointer'],h['raw_row'],h['source']['file_sha256'])
                    refs.pointer(t['source_file'],t['json_pointer'],t['raw_row'],t['source_file_sha256'])
        return result('completed_local_scope', 'All preregistered local source checks passed; not clinical validation.')
    except (AssertionError, KeyError, TypeError, ValueError, IndexError, OSError) as error:
        return result('incorrect_or_unverified', str(error) or type(error).__name__)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):raise RuntimeError('Redirect forbidden')


def run(out):
    frozen = verify_frozen(out)
    with (out/'run_started.json').open('x') as f:
        os.chmod(out/'run_started.json',0o600);json.dump({'started_ns':time.time_ns()},f)
    suite = json.loads((out/'suite.json').read_text());refs = SourceChecks(json.loads((out/'references.json').read_text()))
    rows = [];used_sources = {}
    for case in suite['cases']:
        jar = cookiejar.CookieJar()
        client = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar), NoRedirect())
        turns = [];download = None;started = time.perf_counter()
        def request(method, path, payload=None):
            assert path.startswith('/') and not path.startswith('//')
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(URL+path,data=data,method=method,headers={'Content-Type':'application/json'})
            stamp = time.perf_counter()
            try:r = client.open(req,timeout=45)
            except urllib.error.HTTPError as error:r = error
            with r:
                raw = r.read(3*1024*1024+1);assert len(raw)<=3*1024*1024
                return {'http_status':r.code,'latency_ms':round((time.perf_counter()-stamp)*1000,3),
                        'request_id':r.headers.get('X-Request-ID'), 'attachment':'attachment' in r.headers.get('Content-Disposition',''),
                        'text':raw.decode('utf-8')}
        def send(query):
            r = request('POST','/chat',{'query':query,'answer_mode':'auto'})
            try:body = json.loads(r['text'])
            except ValueError:body = {'non_json_error':True}
            turns.append({'query':query,**{k:v for k,v in r.items() if k!='text'},'body':body})
            return r['http_status'],body
        try:
            if case.get('setup_query'):
                http,body = send(case['setup_query'])
                setup = {'id':case['id'],'kind':'pharmacopoeia',**case['setup_expect']}
                if assess(setup,http,body,refs)['outcome'] != 'completed_local_scope':
                    raise ValueError('Setup did not establish the preregistered source focus')
            http,body = send(case['query'])
            relations = body.get('assistant',{}).get('relations',{})
            if case.get('allow_source_selection') and 'data' not in relations and relations.get('groups'):
                options = [o for o in relations['groups'][0]['options'] if o['source']['relative_file_path']=='etcm_herbs/'+case['herb']+'.json']
                if len(options) == 1:
                    identifier = options[0]['source_record_id'];assert re.fullmatch(r'etcm2-local:[a-f0-9]{64}',identifier)
                    label = {'disease':'疾病富集','herb_target_paths':'成分靶点'}[case['kind']]
                    http,body = send('/relations '+label+' '+identifier)
            research = body.get('research',{});data = research.get('tool_result') or {}
            if case.get('export') and data.get('comparison'):
                snap = data['index_sha256'];assert re.fullmatch('[a-f0-9]{64}',snap)
                before = request('GET','/conversation')['text']
                download = request('GET','/api/research/comparison-report?'+urllib.parse.urlencode({'q':case['query'],'snapshot':snap}))
                assert request('GET','/conversation')['text'] == before, 'Export changed conversation'
            assessment = assess(case,http,body,refs,download)
        except Exception as error:
            assessment = {'outcome':'technical_failure','reason':type(error).__name__+': '+str(error)}
        finally:
            cleanup = request('DELETE','/conversation')
            jar.clear()
        row = {'id':case['id'],'group':case['group'],'role':case['role'],**assessment,
               'elapsed_ms':round((time.perf_counter()-started)*1000,3),'chat_requests':len(turns),
               'cleanup_http':cleanup['http_status'],'turns':turns,'download':download}
        rows.append(row);write(out/'raw_results.json',rows)
        print(json.dumps({k:v for k,v in row.items() if k not in ('turns','download')},ensure_ascii=False),flush=True)
    verify_frozen(out)
    for path,(fingerprint,_) in refs.originals.items():assert sha(path)==fingerprint
    positives = [r for r in rows if r['role']=='positive_task'];boundaries = [r for r in rows if r['role']=='boundary']
    summary = {'status':'completed_screening_not_independent_acceptance','suite_sha256':frozen['suite_sha256'],
               'runtime':frozen['runtime'],'independent_acceptance':False,'observed_user_questions':0,
               'planned':len(suite['cases']),'run':len(rows),'not_run':0,
               'positive_tasks':{'total':len(positives),'outcomes':dict(Counter(r['outcome'] for r in positives))},
               'boundary_tasks':{'total':len(boundaries),'outcomes':dict(Counter(r['outcome'] for r in boundaries))},
               'groups':{group:dict(Counter(r['outcome'] for r in rows if r['group']==group)) for group in dict.fromkeys(r['group'] for r in rows)},
               'cases':[{k:v for k,v in r.items() if k not in ('turns','download')} for r in rows],
               'chat_requests':sum(r['chat_requests'] for r in rows),'cleanup_passed':all(r['cleanup_http']==200 for r in rows),
               'model_comparison':'not_run','provider_calls':'disabled_by_verified_deployment_configuration',
               'latency_note':'Single sequential run including explicit selection/download where specified; no production p95 or capacity claim.',
               'external_model_cost':None,'local_compute_cost':None,'input_and_runtime_unchanged':True,
               'scoring_note':'Positive completion and boundary handling are separate. Some known task templates recur; this is not an independent accuracy estimate.'}
    write(out/'summary.json',summary)
    print(json.dumps({k:summary[k] for k in ('status','positive_tasks','boundary_tasks','chat_requests','cleanup_passed')},ensure_ascii=False))
    return 0 if summary['cleanup_passed'] else 1


def main():
    p = argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','run'))
    p.add_argument('--suite',type=Path,default=ROOT/'eval/business_acceptance_v1.json');p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();out=a.out.resolve()
    assert out.is_relative_to(ROOT/'var') and not out.is_relative_to(SOURCE.resolve()), 'Private output must be under project var'
    if a.action=='prepare':prepare(a.suite.resolve(),out);return 0
    return run(out)


if __name__=='__main__':raise SystemExit(main())
