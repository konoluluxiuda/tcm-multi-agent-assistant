"""Optional Chromium acceptance against an already running local preview."""
import argparse
import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main(args):
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox'])
  context=await browser.new_context();page=await context.new_page();errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto(args.url);await page.locator('#local-research-panel > summary').click()
  await page.locator('[data-research-mode]').select_option('compare')
  await page.locator('#local-research-form input').first.fill('当归')
  await page.locator('[data-second-herb] input').fill('川芎')
  await page.locator('[data-report-question] input').fill('功能与主治')
  await page.locator('#local-research-form [type=submit]').click()
  await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('172 个共同 UniProt')",timeout=30000)
  assert await page.locator('#local-research-output table tr').count()>=10
  assert await page.locator('#local-research-output').get_by_text('当归原始记录',exact=True).count()==20
  async with page.expect_download() as pending:
   await page.get_by_role('button',name='下载报告与出处 JSON').click()
  download=await pending.value
  saved=args.out/'download.json';await download.save_as(saved)
  data=json.loads(saved.read_text());assert data['herbs']==['当归','川芎']
  assert data['targets']['total']==172
  assert all(len(x['sources'])==2 for x in data['targets']['items'])
  saved.unlink()  # Acceptance stores metrics, not source excerpts.

  await page.reload();await page.locator('#local-research-panel > summary').click()
  await page.locator('#local-research-panel details > summary').click()
  await page.locator('#local-research-history button').filter(has_text='回看').first.click()
  await page.wait_for_function("document.querySelector('#local-research-output').innerText.includes('172 个共同 UniProt')")
  assert not errors,errors
  await page.screenshot(path=str(args.out/'browser.png'),full_page=True)
  (args.out/'browser.json').write_text(json.dumps({'status':'passed','real_sources':['当归','川芎'],'target_count':172,'download_verified':True,'history_reloaded':True,'javascript_errors':errors}))
  print('browser passed')
  await browser.close()
if __name__=='__main__':
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--url',default='http://127.0.0.1:7879/')
 parser.add_argument('--browser',type=Path,required=True)
 parser.add_argument('--out',type=Path,required=True)
 args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
 asyncio.run(main(args))
