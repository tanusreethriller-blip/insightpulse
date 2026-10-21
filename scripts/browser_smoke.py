"""Render and exercise the actual UI using Chromium and an isolated real FastAPI/SQLite app.

This bridge avoids any remote network dependency. It does not validate browser-level
CSP enforcement, TLS, or a deployed HTTP server. See docs/TESTING.md for live deployment checks.
"""
from pathlib import Path
import json
import re
import tempfile
import sys
import os
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from reviewpulse.api import create_app
from reviewpulse.config import Settings
from playwright.sync_api import sync_playwright, expect

BASE='http://reviewpulse.test'
OUT=Path(os.environ.get('BROWSER_OUTPUT_DIR',str(ROOT/'test-output')))
OUT.mkdir(parents=True,exist_ok=True)
errors=[]
settings=Settings(data_dir=Path(tempfile.mkdtemp(prefix="reviewpulse-browser-")),ingest_token="test-token-"+"x"*40, public_url=BASE)
with TestClient(create_app(settings),base_url=BASE) as api_client, sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE') or None, headless=True, args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1040}, device_scale_factor=1)
    def api_request(path, options):
        response=api_client.request(options.get('method','GET'), path, content=options.get('body'), headers=options.get('headers',{}))
        return {'status':response.status_code,'body':response.text}
    page.expose_function('_apiRequest',api_request)
    page.on('pageerror', lambda error: errors.append(str(error)))
    root=ROOT/'reviewpulse'/'static'
    content=root.joinpath('index.html').read_text()
    content=re.sub(r'<link[^>]+>', '', content)
    content=re.sub(r'<script[\s\S]*?</script>', '', content)
    page.set_content(content)
    page.add_style_tag(content=root.joinpath('styles.css').read_text())
    page.evaluate("() => { window.fetch = async (path, options={}) => { const r = await window._apiRequest(path,options); return {ok:r.status>=200&&r.status<300,status:r.status,json:async()=>JSON.parse(r.body),text:async()=>r.body}; }; }")
    page.add_script_tag(content=root.joinpath('app.js').read_text())
    expect(page.locator('#workspace')).to_be_visible()
    expect(page.locator('.main-content')).to_contain_text('anyone who can reach this app')
    page.locator('#demo-button').click()
    expect(page.locator('#overview-content')).to_be_visible(timeout=20000)
    expect(page.locator('#overview-content')).to_contain_text('168')
    expect(page.locator('#toast')).not_to_be_visible(timeout=10000)
    page.screenshot(path=str(OUT/'review-pulse-dashboard.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('.nav-item[data-page=reviews]').click()
    page.locator('#sentiment-filter').select_option('negative')
    expect(page.locator('#review-table tbody tr').first).to_be_visible()
    negative_count=page.locator('#review-count').inner_text()
    page.locator('.nav-item[data-page=knowledge]').click()
    page.locator('#add-source-button').click()
    page.locator('#source-title').fill('Test product cancellation policy')
    page.locator('#source-url').fill('https://example.com/help/billing')
    page.locator('#source-content').fill('Subscriptions renew monthly. Cancel a subscription from the Billing page. Cancellation stops the next renewal but does not immediately end paid access.')
    page.locator('#source-form button[type=submit]').click()
    expect(page.locator('#source-list')).to_contain_text('Test product cancellation policy')
    page.locator('#upload-button').click()
    page.locator('#csv-file').set_input_files(str(ROOT/'data'/'synthetic_reviews.csv'))
    page.locator('#dataset-name').fill('Browser CSV validation')
    page.locator('#support-question').fill('How do I cancel my subscription?')
    page.locator('#upload-form button[type=submit]').click()
    expect(page.locator('#upload-dialog')).not_to_be_visible()
    expect(page.locator('#run-status')).to_contain_text('ready',timeout=20000)
    page.locator('.nav-item[data-page=report]').click()
    expect(page.locator('#report-content')).to_contain_text('YourProduct')
    expect(page.locator('#report-content')).to_contain_text('Test product cancellation policy')
    page.locator('#approve-button').click()
    page.locator('#approval-word').fill('APPROVE')
    page.locator('#approval-form button[type=submit]').click()
    expect(page.locator('#approval-dialog')).not_to_be_visible()
    expect(page.locator('#run-status')).to_contain_text('published',timeout=20000)
    expect(page.locator('#approve-button')).to_be_disabled()
    expect(page.locator('#toast')).not_to_be_visible(timeout=10000)
    page.screenshot(path=str(OUT/'review-pulse-report.png'), full_page=True)
    page.locator('.nav-item[data-page=activity]').click()
    expect(page.locator('#local-effects')).to_contain_text('document')
    expect(page.locator('#local-effects')).to_contain_text('draft')
    effects=page.evaluate("fetch('/api/local-effects').then(r=>r.json())")['effects']
    assert len(effects)==2, len(effects)
    page.locator('.nav-item[data-page=overview]').click()
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(OUT/'review-pulse-mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate('({scroll:document.documentElement.scrollWidth,width:innerWidth})')
    assert errors==[], errors
    print(json.dumps({'status':'passed','browser':'Chromium (in-process API bridge; not live network transport)','screens':'overview, reviews, knowledge, CSV upload, report, approval, activity, mobile','negative_review_count_label':negative_count,'local_effects':len(effects),'page_errors':errors},indent=2))
    browser.close()

import shutil
shutil.rmtree(settings.data_dir,ignore_errors=True)
