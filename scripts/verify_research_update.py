"""Accept only a separate tcm-research-next project; protect all existing services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = 'tcm-research-next'
COOKIE = 'research_chat_next_session'
URL = 'http://127.0.0.1:7873'
NAME = PROJECT+'-chat-1'
PROTECTED = ('medical-assistant-current', 'tcm-herb-query-tcm-query-1', 'tcm-research-chat-1')


def docker(*args, code=None):
    return subprocess.check_output(['docker', *args], input=code, text=True).strip()


def inspect(name):
    return json.loads(docker('inspect', name))[0]


def guarded():
    data = inspect(NAME)
    labels = data['Config']['Labels'] or {}
    if labels.get('com.docker.compose.project') != PROJECT or labels.get('com.docker.compose.service') != 'chat':
        raise ValueError('Only the new tcm-research-next/chat project may be mutated')
    return data


def verify(image, output, browser_path):
    import httpx
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    protected = {name: inspect(name) for name in PROTECTED}
    report = {'status': 'running', 'checks': [], 'page_errors': [], 'restarts': 0, 'recreations': 0,
              'docker_hub_published': False, 'clinical_quality_evaluated': False}
    env = dict(os.environ, RESEARCH_IMAGE=image, RESEARCH_PORT='7873', RESEARCH_COOKIE_NAME=COOKIE)
    def compose(enabled):
        local_env=dict(env,ENABLE_PUBMED=str(int(enabled)))
        subprocess.run(['docker','compose','-p',PROJECT,'-f','compose.research.yaml','up','-d',
                        '--no-build','--force-recreate','chat'],cwd=ROOT,env=local_env,check=True)
    def ready():
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            try:
                result=httpx.get(URL+'/ready/research',timeout=10,trust_env=False)
                if result.status_code==200 and guarded()['State'].get('Health',{}).get('Status')=='healthy':
                    return result.json()
            except httpx.HTTPError: pass
            time.sleep(.5)
        raise TimeoutError('New research deployment did not become healthy')
    def passed(label):
        report['checks'].append(label);print(label+': passed',flush=True)
    try:
        # A fresh project is required for the default-off and v1 migration proof.
        existing=docker('ps','-a','--filter','label=com.docker.compose.project='+PROJECT,'--format','{{.Names}}')
        if existing:
            raise FileExistsError('Use a fresh next project; refusing to reset existing storage')
        compose(False); receipt=ready(); data=guarded()
        volume=next(m['Name'] for m in data['Mounts'] if m['Destination']=='/app-memory')
        assert data['HostConfig']['ReadonlyRootfs'] and data['Config']['User']=='appuser'
        assert all(not m['RW'] for m in data['Mounts'] if m['Type']=='bind')
        assert {m['Destination'] for m in data['Mounts'] if m['Type']=='bind'}=={
            '/research-index','/research-sources/high.pdf','/research-sources/full.pdf','/herb-data/herbs.sqlite3'}
        environment=dict(v.split('=',1) for v in data['Config']['Env'] if '=' in v)
        assert environment['ENABLE_PUBMED']=='0' and environment['CHAT_COOKIE_NAME']==COOKIE
        assert environment['CHAT_API_KEY']==environment['EMBEDDING_API_KEY']=='unused-local-placeholder'
        assert environment['CHAT_BASE_URL']==environment['EMBEDDING_BASE_URL']=='http://127.0.0.1:9/v1'
        probe='''import importlib.util,json,os,pathlib
r=pathlib.Path('/app')
assert not (r/'.env').exists() and not any(r.rglob('*.pdf')) and not any(r.rglob('*.sqlite3'))
assert not any(r.rglob('*.pth')) and not (r/'tests').exists()
assert importlib.util.find_spec('torch') is None and importlib.util.find_spec('pymupdf')
assert (r/'tcm/evidence_bundle.py').is_file() and (r/'tcm/literature_evidence.py').is_file()
assert os.getuid()==10001
print(json.dumps({'private_payload_absent':True,'uid':os.getuid()}))'''
        probe_result=json.loads(docker('run','--rm','--network','none','--entrypoint','python',image,'-c',probe))
        assert probe_result['private_payload_absent']
        passed('isolated_nonroot_readonly_image_and_sources_without_private_payload')
        with httpx.Client(base_url=URL,timeout=50,trust_env=False) as a, httpx.Client(base_url=URL,timeout=50,trust_env=False) as b:
            response=a.post('/chat',json={'query':'/pubmed Astragalus[Title/Abstract]'})
            assert response.status_code==503 and response.json()['pubmed']['status']=='pubmed_disabled'
            assert docker('exec',NAME,'python','-c',"import pathlib; assert not pathlib.Path('/app-memory/pubmed.sqlite3').exists(); print('not_created')")=='not_created'
            passed('default_off_does_not_create_literature_database_or_call_provider')
            result=a.post('/chat',json={'query':'/research 黃芪的服用剂量'}).json()['research']
            assert result['status']=='candidates' and result['llm_calls']==0
            hit=result['tool_result']['hits'][0]
            assert a.get('/api/research/page',params={'chunk':hit['chunk_id'],'kind':'evidence','index':0}).content.startswith(b'\x89PNG')
            assert a.get('/api/research/report',params={'q':result['tool_result']['question'],'snapshot':receipt['index_sha256']}).status_code==200
            assert a.post('/chat',json={'query':'/herb BaiBu'}).json()['tcm']['status'] in ('ok','ambiguous_entity')
            for route,payload in (('/chat',{'query':'hello'}),('/upload',{}),('/validate',{})):
                assert a.post(route,json=payload).status_code==403
            passed('existing_herb_pharmacopoeia_preview_export_and_restrictions')
            seed='''import hashlib,json,os,sqlite3
p=os.environ['PUBMED_DB']; assert not os.path.exists(p)
fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
d=sqlite3.connect(p)
d.executescript("""CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT INTO metadata VALUES('store_kind','pubmed_literature_v1');
CREATE TABLE articles(pmid TEXT PRIMARY KEY,doi TEXT,payload TEXT NOT NULL);
CREATE INDEX articles_doi ON articles(doi);
CREATE TABLE search_runs(id TEXT PRIMARY KEY,query TEXT NOT NULL,retrieved_at TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX runs_date ON search_runs(retrieved_at);
CREATE TABLE run_articles(run_id TEXT NOT NULL REFERENCES search_runs(id),pmid TEXT NOT NULL REFERENCES articles(pmid),rank INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,pmid),UNIQUE(run_id,rank));
PRAGMA user_version=1;""")
a={'pmid':'123','doi':'10.0000/fixture','title':'FixtureLegacy','journal':'Fixture','publication_date':'2024','publication_types':['Review'],'abstract':[],'evidence_scope':'metadata_only','full_text_retrieved':False,'url':'https://pubmed.ncbi.nlm.nih.gov/123/'}
r={'status':'ok','query':'migration-fixture-only','retrieved_at':'2024-01-01T00:00:00+00:00','articles':[a]}
payload=json.dumps(r,sort_keys=True); article=json.dumps(a,sort_keys=True)
d.execute('INSERT INTO articles VALUES(?,?,?)',('123',a['doi'],article))
d.execute('INSERT INTO search_runs VALUES(?,?,?,?,?)',('a'*32,r['query'],r['retrieved_at'],'ok',payload))
d.execute('INSERT INTO run_articles VALUES(?,?,?,?)',('a'*32,'123',1,article));d.commit();d.close()
print(hashlib.sha256(payload.encode()).hexdigest())'''
            legacy_hash=docker('exec','-i',NAME,'python','-',code=seed)
            old_id=data['Id']; compose(True); report['recreations']+=1; ready()
            assert guarded()['Id']!=old_id
            query='Astragalus membranaceus[Title/Abstract] AND randomized controlled trial[Publication Type]'
            result=a.post('/chat',json={'query':'/pubmed '+query})
            assert result.status_code==200 and result.json()['pubmed']['status']=='ok', result.status_code
            batch=result.json()['pubmed']['tool_result']; assert batch['articles'] and len(batch['articles'])<=5
            assert all(not r['full_text_retrieved'] and not r['assessment']['quality_appraised'] for r in batch['articles'])
            passed('opt_in_real_pubmed_query_without_tavily_or_model_credentials')
            migration='''import hashlib,json,os,pathlib,sqlite3
p=pathlib.Path(os.environ['PUBMED_DB']);d=sqlite3.connect(p)
assert d.execute('PRAGMA user_version').fetchone()[0]==2
raw=d.execute('SELECT payload FROM search_runs WHERE id=?',('a'*32,)).fetchone()[0]
backups=list((p.parent/'migration_backups').glob('pubmed-v1-*.sqlite3'));assert len(backups)==1
b=sqlite3.connect(backups[0]);assert b.execute('PRAGMA user_version').fetchone()[0]==1
assert b.execute('SELECT payload FROM search_runs WHERE id=?',('a'*32,)).fetchone()[0]==raw
plan=str(d.execute('EXPLAIN QUERY PLAN SELECT payload FROM articles WHERE doi_key=?',('10.0000/fixture',)).fetchall());assert 'articles_doi_key' in plan
print(hashlib.sha256(raw.encode()).hexdigest())'''
            assert docker('exec','-i',NAME,'python','-',code=migration)==legacy_hash
            passed('v1_to_v2_backup_migration_history_unchanged_and_doi_index_used')
            payload={'question':'黄芪的功效','index_sha256':receipt['index_sha256'],'run_id':batch['run_id']}
            before=a.get('/conversation').json()
            exported=a.post('/api/evidence/report',json=payload)
            assert exported.status_code==200 and '研究对象对应关系尚未核验' in exported.text
            assert '物理页码' in exported.text and all(r['pmid'] in exported.text for r in batch['articles'])
            assert a.get('/conversation').json()==before
            assert b.post('/api/evidence/report',json=payload).status_code==401
            b.get('/conversation'); assert b.post('/api/evidence/report',json=payload).status_code==409
            assert a.post('/api/evidence/report',json=dict(payload,index_sha256='0'*64)).status_code==409
            passed('combined_download_bound_to_session_and_sources_without_history_write')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True,executable_path=str(browser_path))
                context=browser.new_context(viewport={'width':1280,'height':900})
                context.add_cookies([{'name':COOKIE,'value':a.cookies[COOKIE],'url':URL,'httpOnly':True,'sameSite':'Lax'},
                                     {'name':'research_chat_session','value':'old-research-marker','url':URL},
                                     {'name':'session_id','value':'legacy-marker','url':URL}])
                page=context.new_page();page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(URL+'/') else route.abort())
                page.goto(URL);page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                card=page.locator('[data-pubmed-status="ok"]');assert card.count()==1
                card.get_by_text('导出药典＋文献证据清单',exact=True).click()
                form=card.locator('.evidence-bundle-form');form.get_by_label('药典查询：').fill('黄芪的功效')
                with page.expect_download() as download: form.get_by_role('button',name='下载证据清单').click()
                file=output/'evidence.md';download.value.save_as(file);os.chmod(file,0o600)
                assert '药典单药证据清单' in file.read_text()
                cookies={c['name']:c['value'] for c in context.cookies()}
                assert cookies['research_chat_session']=='old-research-marker' and cookies['session_id']=='legacy-marker'
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
                page.screenshot(path=str(output/'preview.png'),full_page=True)
                assert not report['page_errors'];browser.close()
            passed('real_browser_download_mobile_and_cookie_namespaces')
            guarded();docker('restart',NAME); report['restarts']+=1; ready()
            assert a.get('/conversation').json()['pubmed']['tool_result']['articles']==batch['articles']
            assert a.post('/chat',json={'query':'/pubmed-last'}).json()['pubmed']['tool_result']['replayed']
            assert a.post('/api/evidence/report',json=payload).status_code==200
            passed('actual_restart_recovers_cookie_batch_flags_and_combined_export')
            previous_id=guarded()['Id'];compose(True);report['recreations']+=1;ready()
            assert guarded()['Id']!=previous_id
            assert a.get('/conversation').json()['pubmed']['tool_result']['articles']==batch['articles']
            assert a.post('/api/evidence/report',json=payload).status_code==200
            passed('actual_recreation_keeps_named_volume_signing_key_and_literature')
            deleted_cookie=a.cookies[COOKIE];assert a.delete('/conversation').status_code==200
            guarded();docker('restart',NAME);report['restarts']+=1;ready()
            with httpx.Client(base_url=URL,timeout=50,trust_env=False,cookies={COOKIE:deleted_cookie}) as old:
                assert old.post('/api/evidence/report',json=payload).status_code in (401,409,410)
                assert old.get('/conversation').json()['messages']==[]
                old.delete('/conversation')
            a.delete('/conversation');b.delete('/conversation')
            passed('deleted_session_does_not_revive_after_restart')
            # Remove only the synthetic migration marker created by this script.
            cleanup='''import os,sqlite3
d=sqlite3.connect(os.environ['PUBMED_DB']);d.execute('PRAGMA foreign_keys=ON')
with d:
 d.execute('DELETE FROM run_articles WHERE run_id=?',('a'*32,));d.execute('DELETE FROM search_runs WHERE id=?',('a'*32,))
 d.execute("DELETE FROM articles WHERE pmid=? AND json_extract(payload,'$.title')=?",('123','FixtureLegacy'))
assert d.execute('PRAGMA foreign_key_check').fetchall()==[]
print('fixture_removed')'''
            assert docker('exec','-i',NAME,'python','-',code=cleanup)=='fixture_removed'
            passed('synthetic_seed_removed_from_preview_catalog')
        for name, original in protected.items():
            current=inspect(name)
            assert current['Id']==original['Id'] and current['State']['StartedAt']==original['State']['StartedAt']
            assert current['State'].get('Health',{}).get('Status')=='healthy'
        passed('all_three_original_services_unchanged_and_healthy')
        final=guarded();metadata=json.loads(docker('image','inspect',image))[0]
        report.update(status='passed',image=image,image_id=final['Image'],image_bytes=metadata['Size'],
                      source_revision=metadata['Config']['Labels']['org.opencontainers.image.revision'],
                      source_tree_sha256=metadata['Config']['Labels']['io.tcm.source-tree-sha256'],
                      url=URL,cookie_name=COOKIE,memory_volume=volume,pubmed_enabled=True,
                      live_pmids=[a['pmid'] for a in batch['articles']],index_sha256=receipt['index_sha256'],
                      report_sha256=hashlib.sha256((output/'evidence.md').read_bytes()).hexdigest())
    except Exception as exc:
        report.update(status='failed', failure_type=type(exc).__name__)
        raise
    finally:
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--browser',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(verify(args.image,args.out.resolve(),args.browser),ensure_ascii=False,indent=2))
