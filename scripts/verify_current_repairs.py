"""Focused native profiling or isolated desktop/mobile checks; never write the main database."""
import argparse
import base64
import cProfile
from dataclasses import fields
import hashlib
from io import BytesIO
import json
from pathlib import Path
import pstats
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from uuid import uuid4

import numpy as np
from PIL import Image

from inspector import auto_inspect as auto, garments
from inspector.images import png_bytes
from inspector.storage import save_inspection
from inspector.web import make_server

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/current-repairs'
SOURCES = ['508bf2a3-0efe-4255-8d7a-c9840d5ff3cb', '6b3562b1-2897-404c-a49a-b4ead868f1d7']


def main_snapshot():
    with sqlite3.connect((ROOT / 'data/inspections.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        return {i: hashlib.sha256(raw.encode()).hexdigest() for i, raw in db.execute('select id,result_json from inspections')}


def profile(phase):
    if phase == 'reference':
        def reference_labels(logits, size):
            import torch
            return torch.nn.functional.interpolate(logits, size=size, mode='bilinear', align_corners=False).argmax(dim=1)
        def reference_evidence(rgb, masks, input_hash):
            original = auto.evidence_png
            auto.evidence_png = png_bytes
            try:
                mask, overlay = auto._draw(rgb, masks, [])
                return png_bytes(rgb), mask, overlay, False
            finally:
                auto.evidence_png = original
        garments._native_labels = reference_labels
        auto.encode_evidence = reference_evidence
    before = main_snapshot()
    data = {'phase': phase, 'reference': 'full native bilinear logits/argmax, PNG level 6, no evidence cache',
            'samples_per_photo': 2, 'cases': []}
    profiler = cProfile.Profile()
    with sqlite3.connect((ROOT / 'data/inspections.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        for identity in SOURCES:
            filename, raw = db.execute('select filename,original_png from inspections where id=?', (identity,)).fetchone()
            for attempt in range(2):
                if not data['cases']:profiler.enable()
                result = auto.inspect_auto(raw)
                profiler.disable()
                report = result.to_dict()
                pixels = {key: hashlib.sha256(np.asarray(Image.open(BytesIO(getattr(result, key + '_png'))))).hexdigest()
                          for key in ['original', 'mask', 'overlay']}
                row = {'source': identity, 'file': filename, 'attempt': attempt, 'processing_ms': result.duration_ms,
                       'timings': result.segmentation['timings'], 'pixels': pixels, 'checks': report['checks'],
                       'score': report['quality_score'], 'profile': report['profile'],
                       'products': [{k: p.get(k) for k in ['region_id', 'bbox', 'intended', 'measurements']}
                                    for p in result.segmentation['products']]}
                data['cases'].append(row)
                print(json.dumps({k: row[k] for k in ['file', 'attempt', 'processing_ms', 'timings']}), flush=True)
    with (OUT / (phase + '-profile.txt')).open('w', encoding='utf-8') as stream:
        pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(35)
    if phase == 'optimized':
        baseline = json.loads((OUT / 'reference.json').read_text(encoding='utf-8'))
        for old, new in zip(baseline['cases'], data['cases'], strict=True):
            for key in ['source', 'attempt', 'pixels', 'checks', 'score', 'profile', 'products']:
                assert old[key] == new[key], (new['file'], key)
        data['identical_native_pixels_measurements_scores'] = True
    assert before == main_snapshot()
    data['historical_reports_preserved'] = len(before)
    (OUT / (phase + '.json')).write_text(json.dumps(data, indent=2), encoding='utf-8')


def browser():
    from websockets.sync.client import connect
    from scripts.verify_submission import CDP
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_photo_scope import audit_image, record
    before = main_snapshot()
    server = make_server(0, OUT / ('browser-' + uuid4().hex + '.sqlite3'))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    cases = []
    ready = auto.inspect_auto(audit_image('separated'))
    cases.append(('ready', ready))
    passing = auto.inspect_auto(audit_image('separated'))
    next(c for c in passing.checks if c.identifier == 'photo.contrast').status = 'Fail'
    assert passing.status == 'Pass'
    cases.append(('ready-with-improvement', passing))
    hidden = auto.inspect_auto(audit_image('separated'))
    warning = next(c for c in hidden.checks if c.identifier == 'product-1.mask_completeness')
    warning.status = 'Manual Review'; warning.reason_code = 'suspected_incomplete_mask'
    warning.explanation = 'Controlled outline uncertainty; the full product mask needs review.'
    cases.append(('hidden-mask', hidden))
    missing = auto.inspect_auto(audit_image('separated'))
    missing.checks = [c for c in missing.checks if c.identifier != 'product-1.clipping']
    cases.append(('missing-required', missing))
    disabled = auto.inspect_auto(audit_image('separated'))
    next(c for c in disabled.checks if c.identifier == 'product-1.contrast').status = 'Disabled'
    cases.append(('disabled-required', disabled))
    selection = auto.inspect_auto(audit_image('merged'))
    next(c for c in selection.checks if c.identifier == 'photo.blur').status = 'Manual Review'
    cases.append(('selection-and-focus', selection))
    historical = auto.inspect_auto(audit_image('separated'))
    historical.pipeline_version = '1.5.0-auto-coverage-dip'; historical.segmentation.pop('provenance')
    save_inspection(historical, 'Historical control.png', server.database)
    corrected = auto.amend_inspection(record(historical), confirm_coverage=True)
    cases.append(('corrected-history', corrected))
    failed = auto.inspect_auto(audit_image('separated'))
    next(c for c in failed.checks if c.identifier == 'photo.noise').status = 'Fail'
    next(c for c in failed.checks if c.identifier == 'product-1.clipping').status = 'Manual Review'
    cases.append(('defect-and-incomplete', failed))
    with sqlite3.connect((ROOT / 'data/inspections.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        for name, filename in [('native-single', 'Plain Blue T-Shirt Studio Portrait.png'),
                               ('native-multi', 'Two models in red and navy T-shirts.png')]:
            row = db.execute('select * from inspections where filename=? order by created_at desc limit 1', (filename,)).fetchone()
            assert row is not None, 'Local native-photo records are required.'
            saved = json.loads(row['result_json'])
            args = {f.name: saved[f.name] for f in fields(auto.InspectionResult) if f.name in saved}
            args.update(checks=[auto.CheckResult(**c) for c in saved['checks']],
                        original_png=row['original_png'], mask_png=row['mask_png'], overlay_png=row['overlay_png'])
            native = auto.InspectionResult(**args)
            assert native.score()['score'] == saved['quality_score']['score'] and native.status == saved['status']
            cases.append((name, native))
    for name, result in cases:save_inspection(result, name + '.png', server.database)
    originals = {r.inspection_id: r.to_json() for _, r in cases}
    original_historical = historical.to_json()
    chrome_profile = ROOT / '.test-tmp' / ('current-repairs-chrome-' + uuid4().hex)
    chrome_profile.mkdir(parents=True)
    command = [r'C:\Program Files\Google\Chrome\Application\chrome.exe', '--headless=new', '--no-sandbox',
               '--disable-gpu', '--in-process-gpu', '--disable-background-networking', '--no-first-run',
               '--no-default-browser-check', '--remote-debugging-port=0', '--remote-allow-origins=*',
               '--user-data-dir=' + str(chrome_profile), 'about:blank']
    process = None; rows = []
    from inspector import web, narration
    from inspector.story import story_options, deterministic_story, validate_story
    original_explain = web.explain
    def local_provider(report):
        options = story_options(report)
        story = deterministic_story(options)
        story['summary'] = options['summary'][-1]
        for key in ('good','actions'):
            for item, choice in zip(story[key], options[key]):item['text'] = choice['texts'][-1]
        story['good'].reverse()
        story = validate_story(story, options)
        return {'source':'groq_validated','story':story,'items':narration.approved_wording(report),
                'score_explanation':narration.score_statement(report['quality_score'])}
    web.explain = local_provider
    def read(path):
        with urllib.request.urlopen(base + path, timeout=15) as response:return response.read()
    try:
        with (OUT / 'browser.log').open('w') as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            for _ in range(100):
                if (chrome_profile / 'DevToolsActivePort').is_file():break
                assert process.poll() is None, 'Chrome exited during startup'
                time.sleep(.1)
            port = (chrome_profile / 'DevToolsActivePort').read_text().splitlines()[0]
            with urllib.request.urlopen('http://127.0.0.1:' + port + '/json', timeout=10) as response:tabs = json.load(response)
            with connect(next(t['webSocketDebuggerUrl'] for t in tabs if t['type'] == 'page'), max_size=32*1024*1024) as socket:
                c = CDP(socket); c.call('Page.enable'); c.call('Runtime.enable'); c.call('Network.enable')
                # Browser receives validated provider alternatives without an external provider call.
                for name, result in cases:
                    for width, height in [(1360, 1000), (390, 844)]:
                        c.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': width == 390})
                        c.navigate(base + '/inspection/' + result.inspection_id)
                        assert c.evaluate('document.documentElement.scrollWidth <= innerWidth+1'), (name, width, 'overflow')
                        c.wait('document.querySelector("#explanation-source").textContent.includes("Groq wording")')
                        action = c.evaluate('document.querySelector(".primary-suggestion a, .primary-suggestion button")?.getBoundingClientRect().bottom || 0')
                        assert action < height, (name, width, 'primary action below fold', action)
                        assert c.evaluate('document.querySelector(".score-label").textContent') == 'Your photo review'
                        assert c.evaluate('document.querySelector(".score-panel").compareDocumentPosition(document.querySelector(".review-photo")) & Node.DOCUMENT_POSITION_FOLLOWING')
                        assert c.evaluate('document.querySelector(".review-photo").getBoundingClientRect().top') < height, (name, width, 'photo below fold')
                        assert c.evaluate('document.querySelectorAll(".action-item").length') <= 3
                        assert c.evaluate('!!document.querySelector("#score-explanation").closest("details")')
                        assert c.evaluate('document.querySelector("#story-summary").textContent') == local_provider(result.to_dict())['story']['summary']
                        assert c.evaluate('Array.from(document.querySelectorAll("[data-story-good]")).map(n=>n.dataset.storyGood)') == [g['id'] for g in local_provider(result.to_dict())['story']['good']]
                        assert c.evaluate('document.querySelector("[data-quality-score]").textContent').startswith(str(result.score()['score']))
                        if name == 'ready':
                            assert c.evaluate('document.querySelector("#story-headline").textContent') == 'Your photo looks good.'
                            assert c.evaluate('document.querySelectorAll(".action-item").length') == 0
                        elif name == 'ready-with-improvement':
                            assert c.evaluate('document.querySelectorAll("[data-action-kind=confirmed]").length') >= 1
                        for check in result.checks:
                            if check.identifier.startswith('product-') and check.required and check.status not in {'Pass', 'Fail'}:
                                assert c.evaluate('!!document.querySelector(' + json.dumps('[data-blocking-finding="' + check.identifier + '"]') + ')')
                        if name == 'hidden-mask':
                            c.evaluate('document.querySelector("[data-blocking-finding] a").click()')
                            c.wait('document.querySelector("#inspection-details").open && document.getElementById("evidence-product-1.mask_completeness").open')
                        if name == 'selection-and-focus':
                            assert c.evaluate('document.querySelectorAll("[data-action-kind=incomplete]").length') >= 2
                            assert c.evaluate('!!document.querySelector("#selection-blocker")')
                        if name == 'defect-and-incomplete':
                            assert c.evaluate('!!document.querySelector("[data-action-kind=confirmed]") && !!document.querySelector("[data-action-kind=incomplete]")')
                        if name == 'corrected-history':
                            assert c.evaluate('document.querySelector(".historical-note").textContent.includes("Saved measurements retained")')
                        c.evaluate('window.scrollTo(0,0)')
                        c.screenshot(OUT / (name + '-' + str(width) + '.png'))
                        (OUT / (name + '-' + str(width) + '-viewport.png')).write_bytes(base64.b64decode(
                            c.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})['data']))
                        if name == 'hidden-mask':
                            c.evaluate('document.querySelector(".product-card").scrollIntoView()')
                            c.screenshot(OUT / ('hidden-mask-card-' + str(width) + '.png'))
                        rows.append({'case': name, 'width': width, 'primary_action_bottom': round(action, 1)})
                c.navigate(base + '/inspection/' + corrected.inspection_id)
                c.evaluate('document.querySelector("#inspection-details").open=true; document.querySelector(".historical-note form").requestSubmit()')
                c.wait('!!document.querySelector(".provenance-note")')
                identity = c.evaluate('location.pathname.split("/").pop()')
                reprocessed = json.loads(read('/report/' + identity + '.json'))
                assert reprocessed['segmentation']['provenance']['operation'] == 'reinspection'
                assert reprocessed['pipeline_version'] == auto.PIPELINE_VERSION
                c.screenshot(OUT / 'reprocessed-mobile.png')
                for identity, raw in originals.items():assert read('/report/' + identity + '.json').decode() == raw
                assert read('/report/' + historical.inspection_id + '.json').decode() == original_historical
    finally:
        if process is not None:process.terminate(); process.wait(timeout=15)
        server.shutdown(); thread.join(timeout=5); server.server_close()
        web.explain = original_explain
    assert main_snapshot() == before
    result = {'layouts': rows, 'historical_reports_preserved': len(before), 'groq_calls': 0,
              'correction_and_reprocessing_distinguished': True, 'validated_provider_ui_update': True, 'isolated_database': str(server.database)}
    (OUT / 'browser.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['reference', 'optimized', 'browser', 'friendly'])
    phase = parser.parse_args().phase
    if phase == 'friendly':OUT = ROOT / 'reports/friendly-results'
    OUT.mkdir(parents=True, exist_ok=True)
    browser() if phase in {'browser','friendly'} else profile(phase)
