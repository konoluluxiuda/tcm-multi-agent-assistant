"""Accept only the isolated tcm-research container; never restart existing services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def inspect(name):
    data = json.loads(docker('inspect', name))[0]
    labels = data['Config']['Labels']
    if labels.get('com.docker.compose.project') != 'tcm-research' or labels.get('com.docker.compose.service') != 'chat':
        raise ValueError('Only the separate tcm-research/chat container may be tested')
    return data


def verify(url, name, output, executable):
    import httpx
    from playwright.sync_api import sync_playwright
    if urlsplit(url).hostname not in ('127.0.0.1', 'localhost'):
        raise ValueError('Local research deployment required')
    if output.exists():
        raise FileExistsError('Use a fresh verification output directory')
    output.mkdir(parents=True)
    initial = inspect(name)
    protected = {n: docker('inspect', '--format', '{{.Id}}', n)
                 for n in ('medical-assistant-current', 'tcm-herb-query-tcm-query-1')}
    report = {'status': 'running', 'checks': [], 'page_errors': [], 'restarts': 0, 'recreations': 0,
              'human_signoff': False, 'clinical_accuracy_evaluated': False, 'docker_hub_published': False}

    def passed(label):
        report['checks'].append(label); print(label + ': passed', flush=True)

    def ready():
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                response = httpx.get(url + '/ready/research', timeout=15, trust_env=False)
                if response.status_code == 200 and inspect(name)['State'].get('Health', {}).get('Status') == 'healthy':
                    return response.json()
            except httpx.HTTPError:
                pass
            time.sleep(.5)
        raise TimeoutError('Research container did not become healthy')

    def turn(client, question, status):
        response = client.post('/chat', json={'query': question})
        assert response.status_code == 200, response.status_code
        result = response.json()
        assert result['research']['status'] == status, result['research']['status']
        assert result['agent'] == 'PHARMACOPOEIA_RESEARCH_AGENT'
        assert result['research']['llm_calls'] == 0
        return result['research']

    try:
        receipt = ready()
        mounts = initial['Mounts']
        binds = [m for m in mounts if m['Type'] == 'bind']
        assert {m['Destination'] for m in binds} == {'/research-index', '/research-sources/high.pdf',
                                                    '/research-sources/full.pdf', '/herb-data/herbs.sqlite3'}
        assert all(not m['RW'] for m in binds)
        memory_volume = next(m['Name'] for m in mounts if m['Destination'] == '/app-memory' and m['Type'] == 'volume')
        assert initial['HostConfig']['ReadonlyRootfs'] and initial['Config']['User'] == 'appuser'
        # Docker Desktop's internal networks suppress published host ports.
        # Use a normal bridge, localhost binding, tool-only gate and no real keys.
        assert initial['HostConfig']['PortBindings']['8000/tcp'][0]['HostIp'] == '127.0.0.1'
        environment = dict(v.split('=', 1) for v in initial['Config']['Env'] if '=' in v)
        assert environment['RESEARCH_TOOLS_ONLY'] == '1'
        assert environment['CHAT_BASE_URL'] == environment['EMBEDDING_BASE_URL'] == 'http://127.0.0.1:9/v1'
        assert environment['CHAT_API_KEY'] == environment['EMBEDDING_API_KEY'] == 'unused-local-placeholder'
        image = json.loads(docker('image', 'inspect', initial['Image']))[0]
        probe = '''import importlib.util,json,os,pathlib
r=pathlib.Path('/app')
assert not (r/'.env').exists()
assert not any(r.rglob('*.pdf')) and not any(r.rglob('*.sqlite3')) and not any(r.rglob('*.pth'))
assert not (r/'agents/image_analysis_agent').exists() and not (r/'agents/rag_agent').exists()
assert importlib.util.find_spec('pymupdf') and importlib.util.find_spec('PIL')
assert importlib.util.find_spec('torch') is None and importlib.util.find_spec('docling') is None
print(json.dumps({'uid':os.getuid(),'private_payload_absent':True,'preview_dependencies':True}))'''
        # Inspect a pristine image, without the data or memory mounts.
        probe_data = json.loads(docker('run', '--rm', '--network', 'none', '--entrypoint', 'python', initial['Image'], '-c', probe))
        assert probe_data['uid'] == 10001
        report.update(image_id=initial['Image'], image_bytes=image['Size'], memory_volume=memory_volume,
                      source_tree_sha256=image['Config']['Labels']['io.tcm.source-tree-sha256'])
        passed('healthy_nonroot_image_readonly_sources_named_memory_and_no_source_code_mount')
        passed('pristine_image_contains_no_pdf_database_env_or_model_weights')
        missing = '''import os
os.environ['PHARMACOPOEIA_INDEX']='/unavailable-research-index'
from app import research_ready
r=research_ready()
assert r.status_code==503 and b'unavailable-research-index' not in r.body
print('missing_source_not_ready')'''
        assert docker('exec', name, 'python', '-c', missing) == 'missing_source_not_ready'
        passed('missing_research_index_is_not_ready_without_leaking_paths')
        with httpx.Client(base_url=url, timeout=40, trust_env=False) as a, httpx.Client(base_url=url, timeout=40, trust_env=False) as b:
            data = turn(a, '/research 黃芪的服用剂量', 'candidates')
            assert a.cookies.get('research_chat_session') and not a.cookies.get('session_id')
            tool = data['tool_result']; hit = tool['hits'][0]
            turn(b, '/research 它的用量', 'clarify_entity')
            page = a.get('/api/research/page', params={'chunk': hit['chunk_id'], 'kind': 'evidence', 'index': 0})
            assert page.status_code == 200 and page.content.startswith(b'\x89PNG')
            params = {'q': tool['question'], 'snapshot': tool['index_sha256']}
            exported = a.get('/api/research/report', params=params)
            assert exported.status_code == 200 and '黃芪 → 黄芪' in exported.text
            assert '剂量 → 用法与用量' in exported.text
            (output / 'evidence.md').write_bytes(exported.content)
            passed('main_graph_query_session_isolation_original_page_and_markdown_export')
            turn(a, '/research 酒黃芪的用量', 'variant_scope_unresolved')
            turn(a, '/research 炙甘草的用量', 'reference_unresolved')
            turn(a, '/research Huangqi的功效', 'name_not_resolved')
            assert a.post('/chat', json={'query': 'hello'}).status_code == 403
            assert a.post('/upload', content=b'').status_code == 403
            assert a.post('/transcribe', content=b'').status_code == 403
            assert a.post('/validate', json={}).status_code == 403
            passed('scope_reference_pinyin_and_unsupported_routes_fail_explicitly')
            herb = a.post('/chat', json={'query': '/herb BaiBu'}).json()
            assert herb['agent'] == 'TCM_HERB_AGENT'
            assert herb['tcm']['status'] in ('ok', 'ambiguous_entity')
            passed('readonly_etcm_tool_still_works')
            turn(a, '/research 甘草的功效', 'candidates')
            before = a.get('/conversation').json()
            assert hit['raw_text'] not in json.dumps(before, ensure_ascii=False)
            docker('restart', name); report['restarts'] += 1; ready()
            after = a.get('/conversation').json()
            assert before['messages'] == after['messages']
            turn(a, '/research 它的用量', 'candidates')
            passed('actual_container_restart_restores_signed_session_and_research_focus')
            old_id = inspect(name)['Id']
            env = {**os.environ, 'RESEARCH_IMAGE': initial['Config']['Image'], 'RESEARCH_PORT': str(urlsplit(url).port)}
            subprocess.run(['docker', 'compose', '-f', str(ROOT / 'compose.research.yaml'),
                            'up', '-d', '--no-build', '--force-recreate', 'chat'], cwd=ROOT, env=env, check=True)
            report['recreations'] += 1; ready()
            assert inspect(name)['Id'] != old_id
            assert any(m.get('Name') == memory_volume for m in inspect(name)['Mounts'])
            turn(a, '/research 它的用量', 'candidates')
            passed('container_recreation_keeps_named_volume_cookie_secret_and_focus')
            a.delete('/conversation')
            docker('restart', name); report['restarts'] += 1; ready()
            assert not a.get('/conversation').json()['messages']
            turn(a, '/research 它的用量', 'clarify_entity')
            b.delete('/conversation'); a.delete('/conversation')
            passed('clear_memory_remains_deleted_after_restart')
        with sync_playwright() as pw:
            options = {'headless': True}
            if executable: options['executable_path'] = str(executable)
            browser = pw.chromium.launch(**options)
            context = browser.new_context(viewport={'width': 1280, 'height': 900})
            context.add_cookies([{'name': 'session_id', 'value': 'unrelated-local-marker', 'url': url}])
            page = context.new_page(); page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
            page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(url + '/')
                       or route.request.url.startswith('blob:' + url + '/') else route.abort())
            page.goto(url); page.wait_for_function('()=>!document.getElementById("message-input").disabled')
            assert '中草药证据研究' in page.inner_text('body')
            assert 'Computer Vision Agents' not in page.inner_text('body')
            page.locator('#message-input').fill('/research 黃芪的服用剂量')
            page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
            card = page.locator('[data-research-status="candidates"]').last
            card.wait_for(); assert '黃芪 → 黄芪' in card.inner_text()
            cookies = {c['name']: c['value'] for c in context.cookies()}
            assert cookies['session_id'] == 'unrelated-local-marker' and cookies['research_chat_session']
            with page.expect_download() as download: card.locator('[data-evidence-export]').click()
            download.value.save_as(str(output / 'browser-evidence.md'))
            card.locator('summary').first.click(); card.get_by_role('button', name='查看原页').first.click()
            image_node = card.locator('img').first
            page.wait_for_function('(im)=>im.complete&&im.naturalWidth>0', arg=image_node.element_handle())
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            context.request.delete(url + '/conversation'); browser.close()
            assert not report['page_errors']
            passed('real_container_browser_profile_evidence_image_download_and_mobile_layout')
        assert protected == {n: docker('inspect', '--format', '{{.Id}}', n) for n in protected}
        report['protected_existing_containers_unchanged'] = True
        report['ready'] = ready(); report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        (output / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:7872')
    parser.add_argument('--container', default='tcm-research-chat-1')
    parser.add_argument('--out', type=Path, default=ROOT / 'var/docker_research_v1/acceptance')
    parser.add_argument('--executable', type=Path)
    args = parser.parse_args()
    result = verify(args.url.rstrip('/'), args.container, args.out, args.executable)
    print(json.dumps({'status': result['status'], 'checks': len(result['checks'])}))
