"""Offline browser acceptance: grouped evidence, navigation and downloadable delivery."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


async def check(args):
    from playwright.async_api import async_playwright
    from tests.test_preset_requests import PresetHTTPTests
    fixture=PresetHTTPTests('test_single_source_table_is_downloadable_without_added_columns')
    fixture.setUp();fixture.client.base_url='http://127.0.0.1';fixture.client.cookies.clear()
    checks=[];errors=[];external=[]
    try:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'],
                env={**os.environ,**({'FONTCONFIG_FILE':str(args.fontconfig)} if args.fontconfig else {})})
            context=await browser.new_context(viewport={'width':1440,'height':1000})
            async def route(route):
                request=route.request
                if urlsplit(request.url).netloc!='127.0.0.1':
                    external.append(request.url);await route.abort();return
                response=await asyncio.to_thread(fixture.client.request,request.method,request.url,
                    headers=await request.all_headers(),content=request.post_data_buffer)
                headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding')}
                await route.fulfill(status=response.status_code,headers=headers,body=response.content)
            await context.route('**/*',route)
            page=await context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto('http://127.0.0.1/')
            await page.wait_for_function('window.tcmSessionReady === true')
            await page.wait_for_function('!document.querySelector("#message-input").disabled')
            assert await page.locator('#research-extension-tools, #research-bookmarks, #local-research-panel').count()==0
            assert await page.locator('.sidebar a[href="/dataops"], .sidebar a[href="/dataops/research"]').count()==0
            checks.append('default_navigation_only_advertises_qa')
            with patch.dict(os.environ,{'SHOW_RESEARCH_EXTENSIONS':'1'}):
                await page.reload();await page.wait_for_function('window.tcmSessionReady === true')
                assert await page.locator('#research-extension-tools').count()==1
                assert await page.locator('#research-extension-tools a[href="/dataops"]').count()==1
                assert '实验与候选功能' in await page.locator('.sidebar').inner_text()
            await page.reload();await page.wait_for_function('window.tcmSessionReady === true')
            await page.wait_for_function('!document.querySelector("#message-input").disabled')
            assert await page.locator('#research-extension-tools').count()==0
            checks.append('developer_extension_navigation_is_explicit_and_reversible')
            assert await page.locator('.sidebar #research-workspaces').count()==1
            assert await page.locator('.chat-container #research-workspaces').count()==0
            sidebar=await page.locator('.sidebar').bounding_box();chat=await page.locator('.chat-container').bounding_box()
            assert sidebar['x']+sidebar['width']<=chat['x']+1
            checks.append('tools_in_left_sidebar_chat_in_center')
            async def send(question):
                await page.locator('#message-input').fill(question)
                await page.locator('#chat-form button[type=submit]').click()
                await page.wait_for_function('!document.querySelector("#message-input").disabled')
            await send('分别告诉我Alpha和Beta的ETCM科名、性味。')
            reply=page.locator('[data-answer-delivery-status]').last
            await reply.locator('.answer-object').last.wait_for()
            assert await reply.locator('.answer-object').count()==2
            assert await reply.locator('.herb-source-card').count()==2
            for card in await reply.locator('.answer-object').all():
                text=await card.inner_text()
                for field in ('科：','性味：'):assert field in text,text
                assert '关联记录' not in text,text
                assert '查询完成' not in text,text
                assert await card.locator('h4').count()==1
                assert await card.locator('.answer-evidence[open]').count()==0
            checks.append('two_herbs_three_fields_each_without_duplicate_cards')
            await send('查询Alpha的ETCM药用部位和药典功能与主治。')
            reply=page.locator('[data-answer-delivery-status]').last
            assert await reply.locator('.answer-object').count()==1
            assert await reply.locator('.answer-source-label').count()==0
            assert set(await reply.locator('.answer-field-source').all_text_contents())=={'ETCM','药典'}
            assert await reply.locator('.answer-fields').count()==1
            text=await reply.inner_text()
            assert '药用部位' in text and '功能与主治' in text
            assert '导出本轮证据' not in text and '检索说明' not in text
            await reply.locator('.answer-evidence > summary').click()
            assert await reply.locator('[data-evidence-export]').count()>=1
            checks.append('cross_source_fields_share_object_but_keep_separate_evidence')
            await send('查询Alpha的性味和剂量')
            reply=page.locator('[data-answer-delivery-status]').last
            assert await reply.locator('.answer-object').count()==1
            assert '性味：' in await reply.inner_text()
            assert '用法与用量' in await reply.inner_text()
            assert '排查请求编号' not in await reply.inner_text()
            assert await reply.locator('.answer-evidence[open]').count()==0
            checks.append('missing_document_field_stays_visible_next_to_combined_properties')
            async def displayed_answers():
                return await page.locator('#chat-body [data-answer-delivery-status]').evaluate_all('''nodes => nodes.map(node => ({
                    status:node.dataset.answerDeliveryStatus,
                    fields:Array.from(node.querySelectorAll('.answer-fields'),item=>item.innerText),
                    objects:Array.from(node.querySelectorAll('.answer-object > h4'),item=>item.textContent),
                    evidence:node.querySelectorAll('.answer-evidence').length
                }))''')
            saved_answers=await displayed_answers()
            assert len(saved_answers)==3
            history_posts=[]
            page.on('request',lambda request:history_posts.append(request.url) if request.method=='POST' else None)
            await page.reload();await page.wait_for_function('window.tcmSessionReady === true')
            assert await displayed_answers()==saved_answers
            assert await page.locator('[data-history-message-index]').evaluate_all(
                'nodes=>nodes.map(node=>node.dataset.historyMessageIndex)')==['1','3','5']
            assert not history_posts
            checks.append('all_saved_answers_restore_in_original_turns_without_post_or_duplicate')
            await send('把Alpha和Beta的ETCM科名、药用部位生成表格。')
            panel=page.locator('.table-delivery').last
            download=panel.get_by_role('link',name='下载 Excel')
            await download.wait_for(state='visible',timeout=30000)
            href=await download.get_attribute('href')
            response=await asyncio.to_thread(fixture.client.get,href)
            assert response.status_code==200 and response.content.startswith(b'PK')
            assert '2 味药材' in await panel.inner_text()
            checks.append('completed_table_has_direct_download')
            await page.screenshot(path=str(args.out/'answers.png'),full_page=True)
            await page.reload();await page.wait_for_function('window.tcmSessionReady === true')
            await page.locator('.table-delivery').last.get_by_role('link',name='下载 Excel').wait_for(state='visible')
            assert await displayed_answers()==saved_answers
            assert await page.locator('#chat-body .table-delivery').count()==1
            assert await page.locator('#chat-body').evaluate('''node => {
                const table=node.querySelector('.table-delivery');
                return Array.from(node.querySelectorAll('[data-answer-delivery-status]')).every(
                    card=>Boolean(card.compareDocumentPosition(table)&Node.DOCUMENT_POSITION_FOLLOWING));
            }''')
            checks.append('table_restores_once_after_original_history_answers')
            await send('我想了解Beta的信息')
            posts_before=len(history_posts)
            await page.reload();await page.wait_for_function('window.tcmSessionReady === true')
            assert await displayed_answers()==saved_answers
            assert len(history_posts)==posts_before
            assert '你想查询哪些资料' in await page.locator('#chat-body .assistant-message').last.inner_text()
            checks.append('clarification_remains_latest_with_prior_answers_in_place')
            # Conflicting values and distinct source identities must never be merged or overwritten.
            grouping=await page.evaluate('''() => {
                const field=value=>({state:'present',value});
                const make=(id,source,value)=>({step_id:id,result_key:'tcm_result',status:'ok',outcome:'complete',
                    route:{request:{name:'<img src=x onerror=alert(1)>'}},result:{status:'ok',dataset_version:'v1',
                    requested_fields:['family'],records:[{source_record_id:source,source:{id:source},fields:{family:field(value)}}]}});
                const answer={status:'partial',answer_steps:[make('s1','a','one'),make('s2','a','two'),make('s3','b','three')],
                    unfinished:[{step_id:'s2',message:'需要核对来源'}]};
                const before=JSON.stringify(answer), host=document.createElement('div');
                renderAnswerDelivery(host,answer,{tcm_result:(node,result)=>{node.textContent=result.records[0].fields.family.value}});
                return {steps:host.querySelectorAll('[data-answer-step]').length,unsafe:host.querySelectorAll('img').length,
                    unchanged:JSON.stringify(answer)===before,text:host.textContent};
            }''')
            assert grouping['steps']==3 and grouping['unsafe']==0 and grouping['unchanged']
            assert '需要核对来源' in grouping['text']
            checks.append('conflicting_values_distinct_sources_and_partial_states_preserved')
            direct=await page.evaluate('''() => {
                const field=value=>({state:'present',value});
                const result={status:'ok',dataset_version:'v1',requested_fields:['properties','flavors'],records:[{
                    source_record_id:'s1',source:{id:'s1'},name_zh_candidates:[{name_zh:'白术'}],
                    fields:{name_pinyin:field('BaiZhu'),properties:field('温'),flavors:{state:'missing',value:null}}}]};
                const host=document.createElement('div');document.body.append(host);
                renderHerbResult(host,result,()=>{});
                const hit={name:'白术',raw_name:'白术',label:'用法与用量',text:'fixture dose',raw_text:'fixture dose',
                    review_status:'unreviewed',field_overlay_ids:[],entry_overlay_ids:[],raw_flags:[],evidence:[],title_evidence:[]};
                const research=document.createElement('div');document.body.append(research);
                renderResearchResult(research,{status:'candidates',tool_result:{hits:[hit],query_interpretation:{
                    field_matches:[{query_term:'剂量',field:'用法与用量'}]}}});
                const output={herbHeads:host.querySelectorAll('h4').length,herb:host.innerText,
                    researchHeads:research.querySelectorAll('.answer-object > h4').length,research:research.innerText,
                    explanationSaved:research.textContent.includes('检索说明')};
                host.remove();research.remove();return output;
            }''')
            assert direct['herbHeads']==1 and '药味：来源未提供' in direct['herb']
            assert 'BaiZhu' not in direct['herb'] and '来源与原始字段' not in direct['herb']
            assert direct['researchHeads']==1 and direct['research'].count('白术')==1
            assert '检索说明' not in direct['research'] and direct['explanationSaved']
            checks.append('legacy_scalar_routes_use_same_compact_display_without_hiding_missing_fields')
            quantity=await page.evaluate(r'''() => {
                const original=String.raw`【用法与用量】 $6\sim 12\mathrm{g}$`;
                const hit={name:'白术',raw_name:'白术',label:'用法与用量',text:original,raw_text:original,
                    review_status:'unreviewed',field_overlay_ids:[],entry_overlay_ids:[],raw_flags:[],evidence:[],title_evidence:[]};
                const before=JSON.stringify(hit),host=document.createElement('div');document.body.append(host);
                renderResearchResult(host,{status:'candidates',tool_result:{hits:[hit]}});
                const values=[String.raw`$0.5\sim 1.5\mathrm{mg}$`,String.raw`$6\mathrm{g}$`,
                    String.raw`$6\times 12\mathrm{g}$`,String.raw`$\frac{6}{12}\mathrm{g}$`,
                    String.raw`$$6\sim 12\mathrm{g}$$`,
                    String.raw`$6\sim 12\mathrm{g}$<img src=x onerror=alert(1)>`];
                const output={visible:host.innerText,originalSaved:host.textContent.includes(original),
                    unchanged:before===JSON.stringify(hit),values:values.map(readableEvidenceText),
                    heads:host.querySelectorAll('.answer-object > h4').length};
                host.remove();return output;
            }''')
            assert '6–12 g' in quantity['visible'] and '\\mathrm' not in quantity['visible']
            assert '【用法与用量】' not in quantity['visible']
            assert quantity['heads']==1 and quantity['visible'].count('白术')==1
            assert quantity['originalSaved'] and quantity['unchanged']
            assert quantity['values']==['0.5–1.5 mg','6 g',r'$6\times 12\mathrm{g}$',
                r'$\frac{6}{12}\mathrm{g}$',r'$$6\sim 12\mathrm{g}$$','6–12 g<img src=x onerror=alert(1)>']
            checks.append('quantity_formula_readable_without_changing_values_units_or_source_text')
            await page.screenshot(path=str(args.out/'desktop.png'),full_page=True)
            await page.set_viewport_size({'width':390,'height':844})
            assert await page.locator('#toggle-tools').is_visible()
            assert await page.locator('.side-tools').is_hidden()
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            await page.locator('#toggle-tools').click()
            assert await page.locator('.side-tools').is_visible()
            await page.locator('#toggle-tools').click()
            await page.screenshot(path=str(args.out/'mobile.png'),full_page=True)
            checks.append('mobile_menu_and_no_horizontal_overflow')
            assert not errors,errors
            assert not external,external
            await browser.close()
        (args.out/'report.json').write_text(json.dumps({'status':'passed','checks':checks,'javascript_errors':errors,
            'external_requests':external,'model_calls':0},ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':'passed','checks':checks},ensure_ascii=False))
    finally:fixture.doCleanups()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--fontconfig',type=Path,help='Optional local font configuration for screenshots')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    import dotenv
    from scripts.run_offline_ci import test_environment
    with patch.dict(os.environ,test_environment(),clear=True),patch.object(dotenv,'load_dotenv',return_value=False):
        asyncio.run(check(args))


if __name__=='__main__':main()
