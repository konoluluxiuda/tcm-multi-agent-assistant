"""Real browser + graph + restart acceptance; synthetic literature, models forbidden."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time

from scripts.run_offline_ci import ROOT, test_environment


def serve(fd):
    import tcm.agent_pubmed as tool
    from tests.test_pubmed import client
    from tests.test_literature_evidence import parsed, relation
    class FixtureClient:
        def search(self, query, limit=5):
            options = {'ids': []} if query == 'fixture-empty' else {'xml': '<bad'} if query == 'fixture-error' else {}
            result = client(**options).search(query, limit)
            if query == 'fixture-relations':
                result = parsed(relation('RetractionIn'))
                result['articles'][0]['relations'][0]['ref_source'] = '<img src=x onerror="window.sourceExecuted=true"> Source relation'
            elif query == 'fixture-peer':
                result = parsed(pmid='456',doi='HTTPS://DOI.ORG/10.0000/FIXTURE')
            elif query == 'fixture-legacy':
                result['articles'][0].pop('assessment',None)
            if result['articles']:
                result['articles'][0]['title'] = '<img src=x onerror="window.sourceExecuted=true"> Fixture title'
                result['articles'][0]['abstract'][0]['text'] = '<script>window.sourceExecuted=true</script> Fixture abstract'
            return result
    tool.PubMedClient = FixtureClient
    if os.getenv('VERIFY_BUNDLE_INDEX'):
        from tests.test_mineru_chat import Service
        from tcm.agent_research import runtime as research_runtime
        service=Service(Path(os.environ['VERIFY_BUNDLE_INDEX']))
        research_runtime.get_service=lambda:service
    from scripts.verify_tcm_chat_ui import serve as main_serve
    main_serve(fd)


def verify(output, python, browser_path):
    import httpx
    import tempfile
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'running', 'synthetic_literature': True, 'llm_calls': 0,
              'actual_server_restarts': 0, 'checks': [], 'page_errors': []}
    with tempfile.TemporaryDirectory(prefix='pubmed-chat-ui-') as temp, socket.socket() as sock:
        from tests.test_mineru_retrieval import fixture
        from scripts.build_mineru_retrieval import build
        fixture(Path(temp)/'view'); build(Path(temp)/'view',Path(temp)/'index')
        sock.bind(('127.0.0.1', 0)); url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        env = test_environment()
        env.update(ENABLE_PUBMED='1', RESEARCH_TOOLS_ONLY='1', CHAT_MEMORY_DB=temp+'/memory.sqlite3',
                   PUBMED_DB=temp+'/literature.sqlite3', PYTHONPYCACHEPREFIX=temp+'/pycache',
                   ENABLE_PHARMACOPOEIA_RESEARCH='1', VERIFY_BUNDLE_INDEX=temp+'/index')
        server, log = None, None
        def stop():
            if server and server.poll() is None:
                server.terminate(); server.wait(timeout=10)
            if log: log.close()
        def start():
            nonlocal server, log
            log = (output/f'server-{report["actual_server_restarts"]}.log').open('w')
            server = subprocess.Popen([str(python), '-m', 'scripts.verify_pubmed_chat_ui', '--serve', str(sock.fileno())],
                cwd=ROOT, env=env, pass_fds=(sock.fileno(),), stdout=log, stderr=log)
            with httpx.Client(trust_env=False, timeout=.5) as api:
                deadline = time.monotonic()+30
                while time.monotonic()<deadline:
                    try:
                        if api.get(url+'/health').status_code == 200: return
                    except httpx.HTTPError: pass
                    if server.poll() is not None: break
                    time.sleep(.1)
            raise RuntimeError('PubMed browser server did not start; inspect private log')
        try:
            start()
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, executable_path=str(browser_path))
                context = browser.new_context(viewport={'width': 1280, 'height': 900})
                def page_for(ctx):
                    page=ctx.new_page()
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    page.route('**/*', lambda route: route.continue_() if route.request.url.startswith(url+'/') else route.abort())
                    page.goto(url)
                    page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                    return page
                page=page_for(context)
                def submit(query, status):
                    old=page.locator('[data-pubmed-status]').count()
                    page.locator('#message-input').fill(query)
                    with page.expect_response(lambda r:r.url==url+'/chat') as response:
                        page.locator('#chat-form').evaluate('(form)=>form.requestSubmit()')
                    data=response.value.json()
                    assert data['pubmed']['status']==status, data
                    page.wait_for_function('(n)=>document.querySelectorAll("[data-pubmed-status]").length===n',arg=old+1)
                    page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                    return page.locator('[data-pubmed-status]').last
                card=submit('/pubmed fixture-herb','ok')
                assert card.locator('.pubmed-article').count()==1
                assert card.locator('a[href="https://doi.org/10.0000%2Ffixture"]').count()==1
                assert card.get_by_role('link',name='PMID 123',exact=True).get_attribute('href')=='https://pubmed.ncbi.nlm.nih.gov/123/'
                card.get_by_text('查看来源摘要',exact=True).click()
                assert 'Fixture abstract' in card.inner_text()
                assert card.locator('img,script').count()==0
                assert page.evaluate('window.sourceExecuted===true') is False
                report['checks'].append('evidence_scope_links_and_html_as_text')
                card.screenshot(path=str(output/'evidence.png'))
                submit('/pubmed-last','ok')
                report['checks'].append('same_session_local_replay')
                card=submit('/pubmed fixture-relations','ok')
                assert '已撤稿（来源标注）' in card.inner_text()
                assert '来源关系 RetractionIn' in card.inner_text()
                assert card.get_by_role('link',name='PMID 456',exact=True).get_attribute('href')=='https://pubmed.ncbi.nlm.nih.gov/456/'
                assert card.locator('img,script').count()==0
                assert not page.evaluate('window.sourceExecuted===true')
                card.screenshot(path=str(output/'relations.png'))
                report['checks'].append('source_relationship_flags_and_links')
                card.get_by_text('导出药典＋文献证据清单',exact=True).click()
                form=card.locator('.evidence-bundle-form');form.get_by_label('药典查询：').fill('示例叶炭的功效')
                before=page.request.get(url+'/conversation').json()
                with page.expect_download() as download:
                    form.get_by_role('button',name='下载证据清单').click()
                download.value.save_as(output/'bundle.md')
                bundle=(output/'bundle.md').read_text()
                for value in ('研究对象对应关系尚未核验','药典单药证据清单','测试用途','已撤稿','物理页码（从 1 开始）：3'):
                    assert value in bundle,value
                assert '<script>' not in bundle and 'Fixture abstract' not in bundle
                assert page.request.get(url+'/conversation').json()==before
                report['checks'].append('combined_private_download_keeps_history_and_sources')
                old_form=form.element_handle()
                card=submit('/pubmed fixture-peer','ok')
                assert '相同DOI的其他PMID：123' in card.inner_text()
                assert card.locator('.pubmed-article').count()==1
                report['checks'].append('cross_batch_doi_candidate_without_merge')
                with page.expect_response(lambda r:r.url==url+'/api/evidence/report') as stale:
                    old_form.evaluate('(form)=>form.requestSubmit()')
                assert stale.value.status==409
                report['checks'].append('old_card_export_rejected_after_batch_switch')
                card=submit('/pubmed-last','ok')
                assert '相同DOI的其他PMID：123' in card.inner_text()
                report['checks'].append('identity_flags_survive_historical_replay')
                other=browser.new_context(); other_page=page_for(other)
                assert other_page.locator('[data-pubmed-status]').count()==0
                other.close(); report['checks'].append('browser_session_isolation')
                stop(); report['actual_server_restarts']+=1; start()
                page.reload(); page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                assert page.locator('[data-pubmed-status="ok"]').count()==1
                assert '历史批次回放' in page.locator('[data-pubmed-status]').inner_text()
                report['checks'].append('actual_process_restart_restores_cards')
                restored=page.locator('[data-pubmed-status]')
                restored.get_by_text('导出药典＋文献证据清单',exact=True).click()
                restored_form=restored.locator('.evidence-bundle-form');restored_form.get_by_label('药典查询：').fill('示例叶炭的功效')
                with page.expect_download() as restored_download:
                    restored_form.get_by_role('button',name='下载证据清单').click()
                restored_download.value.save_as(output/'bundle-after-restart.md')
                assert '同DOI的其他PMID候选' in (output/'bundle-after-restart.md').read_text()
                report['checks'].append('combined_export_after_actual_restart')
                submit('/pubmed-clear','context_cleared'); submit('/pubmed-last','no_active_run')
                report['checks'].append('clear_does_not_reuse_previous_batch')
                submit('/pubmed fixture-empty','no_results'); submit('/pubmed fixture-error','error')
                report['checks'].append('zero_results_and_upstream_error_visible')
                submit('/pubmed fixture-herb','ok')
                page.get_by_role('button',name='清除会话').click()
                page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                page.reload(); page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                assert page.locator('[data-pubmed-status]').count()==0
                report['checks'].append('delete_session_removes_replay_reference')
                card=submit('/pubmed fixture-legacy','ok')
                assert '旧批次未记录本版关系核查' in card.inner_text()
                report['checks'].append('legacy_unchecked_batch_is_not_claimed_safe')
                page.set_viewport_size({'width':390,'height':844})
                submit('/pubmed fixture-herb','ok')
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
                report['checks'].append('mobile_layout')
                assert not report['page_errors'], report['page_errors']
                browser.close()
            report['status']='passed'
        finally:
            stop()
            (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve',type=int)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--server-python',type=Path)
    parser.add_argument('--browser',type=Path)
    args=parser.parse_args()
    if args.serve is not None: serve(args.serve)
    else:
        if not all((args.out,args.server_python,args.browser)): parser.error('--out, --server-python and --browser required')
        print(json.dumps(verify(args.out,args.server_python,args.browser),ensure_ascii=False))
