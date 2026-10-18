"""Verify only the isolated 7873 research preview, preserving its named volume."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
NAME = 'tcm-research-next-chat-1'
URL = 'http://127.0.0.1:7873'
COOKIE = 'research_chat_next_session'
PROTECTED = ('medical-assistant-current', 'tcm-herb-query-tcm-query-1', 'tcm-research-chat-1')


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def inspect(name):
    return json.loads(docker('inspect', name))[0]


def verify(image, output, browser_path):
    import httpx
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {'status':'running','checks':[],'docker_hub_published':False,'clinical_quality_evaluated':False}
    old = inspect(NAME)
    assert old['Config']['Labels']['com.docker.compose.project']=='tcm-research-next'
    protected = {n:inspect(n) for n in PROTECTED}
    volume = next(m['Name'] for m in old['Mounts'] if m['Destination']=='/app-memory')
    def passed(label):
        report['checks'].append(label);print(label+': passed',flush=True)
    def ready():
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            try:
                response=httpx.get(URL+'/ready/research',timeout=10,trust_env=False)
                if response.status_code==200 and inspect(NAME)['State'].get('Health',{}).get('Status')=='healthy':
                    return response.json()
            except httpx.HTTPError:pass
            time.sleep(.5)
        raise TimeoutError('New preview did not become ready')
    try:
        with httpx.Client(base_url=URL,timeout=50,trust_env=False) as a, httpx.Client(base_url=URL,timeout=50,trust_env=False) as b:
            baseline=a.post('/chat',json={'query':'/research 黄芪的功效'})
            assert baseline.status_code==200 and baseline.json()['research']['status']=='candidates'
            history=a.get('/conversation').json()['messages']
            env=dict(os.environ,RESEARCH_IMAGE=image,RESEARCH_PORT='7873',RESEARCH_COOKIE_NAME=COOKIE,
                     ENABLE_PUBMED='1',ENABLE_NATURAL_ROUTING='1')
            subprocess.run(['docker','compose','-p','tcm-research-next','-f','compose.research.yaml',
                            'up','-d','--no-build','chat'],cwd=ROOT,env=env,check=True)
            receipt=ready();new=inspect(NAME)
            assert next(m['Name'] for m in new['Mounts'] if m['Destination']=='/app-memory')==volume
            assert a.get('/conversation').json()['messages']==history
            passed('image_update_preserves_existing_volume_cookie_and_committed_history')
            def turn(text,client=a):
                r=client.post('/chat',json={'query':text});assert r.status_code==200,(text,r.status_code)
                return r.json()
            for text in ('黄芪有什么作用','黄芪的功效有哪些'):
                data=turn(text);assert data['research']['status']=='candidates' and data['routing']['mode']=='natural'
            assert turn('用量呢')['research']['resolved_question']=='黄芪的用量'
            assert turn('出处呢')['research']['status']=='candidates'
            assert turn('用量呢',b)['research']['status']=='clarify_entity'
            passed('natural_pharmacopoeia_fields_followups_source_and_session_isolation')
            stats=turn('支持多少药材')['assistant']
            assert stats['status']=='ok' and stats['llm_calls']==stats['network_calls']==0
            herbs=stats['herbs'];research=stats['pharmacopoeia']
            assert herbs['source_record_count']==2076 and research['entry_count']==616 and research['field_chunk_count']==8654
            assert herbs['efficacy_present_records']+herbs['efficacy_missing_records']==2076
            assert len(herbs['raw_efficacy_values'])==min(20,herbs['raw_efficacy_value_count'])
            assert '不等于' in stats['message'] and '不能相加' in stats['message']
            assert turn('有哪些功效分类')['assistant']['topic']=='overview'
            assert turn('你能做什么')['assistant']['topic']=='capabilities'
            assert turn('用量呢')['research']['status']=='candidates'
            passed('live_inventory_raw_classification_missing_counts_and_capabilities_preserve_focus')
            assert turn('什么是归经')['assistant']['status']=='clarification_needed'
            assert turn('找黄芪相关文献')['assistant']['status']=='clarification_needed'
            assert turn('黄芪的药典记载和相关文献')['assistant']['topic']=='clarify_sources'
            assert a.post('/chat',json={'query':'hello'}).status_code==403
            for route in ('/upload','/validate'):
                assert a.post(route,json={}).status_code==403
            passed('concept_translation_and_mixed_sources_clarify_and_general_profile_stays_restricted')
            data=turn('在PubMed检索 Astragalus membranaceus[Title/Abstract] AND randomized controlled trial[Publication Type]')
            assert data['pubmed']['status']=='ok';batch=data['pubmed']['tool_result']
            turn('数据概览')
            body={'question':'黄芪的功效','index_sha256':receipt['index_sha256'],'run_id':batch['run_id']}
            assert a.post('/api/evidence/report',json=body).status_code==200
            passed('natural_real_pubmed_and_combined_export_after_inventory')
            subprocess.run(['docker','restart',NAME],check=True,capture_output=True);ready()
            assert a.get('/conversation').json()['pubmed']['tool_result']['articles']==batch['articles']
            assert a.post('/api/evidence/report',json=body).status_code==200
            passed('actual_restart_recovers_session_and_offline_literature_snapshot')
            errors=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,executable_path=str(browser_path))
                context=browser.new_context(viewport={'width':1280,'height':900})
                context.add_cookies([{'name':COOKIE,'value':a.cookies[COOKIE],'url':URL,'httpOnly':True,'sameSite':'Lax'}])
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(URL+'/') else r.abort())
                page.goto(URL);page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                has_workspaces = page.locator('#research-workspaces').count() > 0
                if has_workspaces:
                    page.get_by_role('tab',name='数据概览',exact=True).click()
                    page.get_by_role('button',name='查看能力说明',exact=True).click()
                else:
                    page.get_by_role('button',name='功能说明',exact=True).click()
                    assert page.locator('#message-input').input_value()=='你能做什么'
                    page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
                page.locator('[data-assistant-topic="capabilities"]').wait_for()
                page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                if has_workspaces:
                    page.get_by_role('button',name='更新数据概览',exact=True).click()
                else:
                    page.get_by_role('button',name='数据概览',exact=True).click()
                    page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
                card=page.locator('[data-assistant-topic="overview"]');card.wait_for()
                assert '2076' in card.inner_text() and card.locator('li').count()==len(herbs['raw_efficacy_values'])
                page.wait_for_function('()=>!document.getElementById("message-input").disabled')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
                page.screenshot(path=str(output/'preview.png'),full_page=True)
                passed('real_browser_navigation_inventory_and_mobile_width')
                # Only a synthetic frontend response; never insert this label in a live database.
                def malicious(route):
                    route.fulfill(json={'status':'success','response':'合成分类测试','agent':'ASSISTANT_INFO_AGENT',
                                        'assistant':{'topic':'overview','herbs':{'raw_efficacy_values':[
                                            {'value':'<img src=x onerror="window.badCategory=1">','record_count':1}]}}})
                page.route('**/chat',malicious)
                page.locator('#message-input').fill('合成渲染测试');page.locator('#chat-form').evaluate('(f)=>f.requestSubmit()')
                page.wait_for_function('()=>document.querySelectorAll("[data-assistant-topic=overview]").length===2')
                test=page.locator('[data-assistant-topic="overview"]').last
                assert test.locator('img').count()==0 and '<img' in test.inner_text() and page.evaluate('window.badCategory===undefined')
                assert not errors,errors
                browser.close()
            passed('source_classification_labels_are_literal_text_without_script_execution')
            a.delete('/conversation');b.delete('/conversation')
        for name,before in protected.items():
            current=inspect(name)
            assert current['Id']==before['Id'] and current['State']['StartedAt']==before['State']['StartedAt']
            assert current['State'].get('Health',{}).get('Status')=='healthy'
        passed('all_three_original_services_unchanged_and_healthy')
        build=json.loads((ROOT/'var/natural_routing_v1/build.json').read_text())
        report.update(status='passed',url=URL,image=image,image_id=inspect(NAME)['Image'],
                      source_revision=build['base_revision'],source_tree_sha256=build['source_tree_sha256'],
                      memory_volume_preserved=True,real_restarts=1,source_record_count=herbs['source_record_count'],
                      efficacy_present_records=herbs['efficacy_present_records'],efficacy_missing_records=herbs['efficacy_missing_records'],
                      raw_efficacy_value_count=herbs['raw_efficacy_value_count'],pharmacopoeia_entries=research['entry_count'],
                      pharmacopoeia_chunks=research['field_chunk_count'],page_errors=errors)
    except Exception as exc:
        report.update(status='failed',failure_type=type(exc).__name__);raise
    finally:
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--browser',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(verify(args.image,args.out.resolve(),args.browser),ensure_ascii=False,indent=2))
