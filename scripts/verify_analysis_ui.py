"""Real-route scientific UI checks, preserving external-injection failures separately."""
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import time
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
report = {'checks': [], 'errors': [], 'external_requests': []}
process = subprocess.Popen([sys.executable, 'run_nivram.py', '--no-browser'], cwd=ROOT,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
browser = None
def wait_until(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise TimeoutError('Expected UI state did not become ready.')
try:
    url = None
    for _ in range(4):
        line = process.stdout.readline()
        match = re.search(r'http://127\.0\.0\.1:\d+/', line)
        if match:
            url = match.group(); break
    if url is None:
        raise RuntimeError('Local server failed to start.')
    with sync_playwright() as playwright:
        edge = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
        browser = playwright.chromium.launch(headless=True, executable_path=str(edge))
        page = browser.new_page(viewport={'width': 1366, 'height': 768})
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        def route(request):
            location = request.request.url
            if location.startswith(url) or location.startswith(('blob:', 'data:')):
                request.continue_()
            else:
                report['external_requests'].append(location)
                request.abort()
        page.route('**/*', route)
        page.goto(url)
        wait_until(lambda: page.locator('#request-status').inner_text() == 'Ready')
        page.click('#import-button'); page.fill('#structure-input', 'CCO'); page.click('#load-structure')
        wait_until(lambda: page.locator('#property-smiles').input_value() == 'CCO')
        assert page.locator('#property-formula').inner_text() == 'C2H6O'
        assert page.locator('#property-mass').inner_text().startswith('46.0690')
        assert page.locator('#property-exact-mass').inner_text().startswith('46.041865')
        assert page.locator('#property-logp').inner_text() == '-0.001'
        report['checks'].append({'id': 'properties-real-ui', 'status': 'passed'})
        before = page.evaluate('window.nivramDocument.getSnapshot().document')
        page.get_by_text('IUPAC Name', exact=True).click(); page.click('#calculate-name')
        wait_until(lambda: page.locator('#name-result').inner_text() == 'ethanol')
        assert 'generated unverified' in page.locator('#name-details').inner_text()
        assert page.evaluate('window.nivramDocument.getSnapshot().document') == before
        report['checks'].append({'id': 'naming-ui-and-nonmutation', 'status': 'passed'})
        page.get_by_text('Physiological pH references', exact=True).click()
        wait_until(lambda: page.locator('#physiology-context option').count() == 20)
        page.select_option('#physiology-context', index=1)
        assert '7.35–7.45' in page.locator('#physiology-detail').inner_text()
        assert 'Arterial' in page.locator('#physiology-detail').inner_text() or 'arterial' in page.locator('#physiology-detail').inner_text()
        texts = page.locator('#physiology-context option').all_text_contents()
        assert not any('final urine' in text.lower() for text in texts)
        report['checks'].append({'id': 'physiology-context-precision-and-scope', 'status': 'passed'})
        assert not report['errors'], report['errors']
        report['checks'].append({'id': 'javascript-errors', 'status': 'passed'})
        report['checks'].append({'id': 'no-external-requests', 'status': 'failed' if report['external_requests'] else 'passed'})
        browser.close(); browser = None
except Exception as error:
    report['errors'].append(type(error).__name__ + ': ' + str(error))
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    destination = ROOT / 'verification' / 'analysis-ui.json'
    destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
if report['errors'] or any(check['status'] != 'passed' for check in report['checks']):
    raise SystemExit(1)
