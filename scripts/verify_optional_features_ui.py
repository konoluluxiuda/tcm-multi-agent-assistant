"""Verify the running medical page with optional features off (mock chat only)."""
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def main():
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def intercept(route):
            parsed = urlparse(route.request.url)
            if parsed.path == '/chat' and parsed.hostname == '127.0.0.1':
                route.fulfill(json={'status':'success','response':'offline UI test','agent':'CONVERSATION_AGENT'})
            elif 'marked' in parsed.path:
                route.fulfill(content_type='application/javascript',body='window.marked={parse:(text)=>text};')
            elif parsed.hostname == '127.0.0.1':
                route.continue_()
            else:
                # External presentation assets are not needed for this functional check.
                route.abort()
        page.route('**/*', intercept)
        page.goto('http://127.0.0.1:7860',wait_until='domcontentloaded')
        flags = page.request.get('http://127.0.0.1:7860/features').json()
        assert all(not state['available'] for state in flags.values())
        checks.append('running service reports all optional features off')
        assert page.locator('#image-upload').is_disabled()
        assert page.locator('.file-upload-container').is_hidden()
        checks.append('image upload hidden and disabled')
        assert page.locator('#voice-record-btn').count() == 0
        checks.append('voice recording control absent')
        page.locator('#message-input').fill('offline browser test')
        with page.expect_response(lambda response: response.url.endswith('/chat')):
            page.locator('#chat-form').evaluate('(form) => form.requestSubmit()')
        page.wait_for_function("() => document.getElementById('chat-body').textContent.includes('offline UI test')")
        assert page.locator('.play-tts-btn').count() == 0
        checks.append('text reply renders without speech controls (mock reply)')
        assert not errors, errors
        checks.append('no browser JavaScript errors')
        browser.close()
    result = {'passed':True,'checks':checks,'chat_response':'mocked; no paid APIs called'}
    out=Path('audits/optional_features/ui_checks_2026-10-12.json')
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
