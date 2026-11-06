"""Focused live Chrome verification against the existing Framecheck instance on port 8505."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request
from uuid import uuid4

from websockets.sync.client import connect
from scripts.verify_submission import CDP

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/one-click-redesign/live'
BASE = 'http://127.0.0.1:8505'


def get_json(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.load(response)


def wait(c, expression, seconds=90):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        try:
            if c.evaluate(expression): return
        except RuntimeError:
            pass
        time.sleep(.15)
    raise AssertionError('Browser condition timed out: '+expression)


def export(identity, name):
    report=get_json('/report/'+identity+'.json')
    folder=OUT/name;folder.mkdir(exist_ok=False)
    (folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    for kind in ['original','mask','overlay']:
        with urllib.request.urlopen(f'{BASE}/evidence/{identity}/{kind}.png',timeout=10) as response:
            (folder/(kind+'.png')).write_bytes(response.read())
    return report


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'verification.json').exists():
        assert not json.loads((OUT/'verification.json').read_text()).get('original_ids_preserved'),'Completed evidence already exists.'
    profile=ROOT/'.test-tmp'/('one-click-chrome-'+uuid4().hex);profile.mkdir()
    args=[r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new','--no-sandbox',
          '--disable-gpu-sandbox','--disable-gpu','--in-process-gpu','--no-first-run',
          '--no-default-browser-check','--remote-debugging-port=0','--remote-allow-origins=*',
          '--user-data-dir='+str(profile),'about:blank']
    cases=[('clear-shoe',ROOT/'reports/one-click-redesign/source-audit/40a0e7f4-881c-4a4c-8901-92957a94fe01.png','Pass',1),
           ('mixed-products-controlled',ROOT/'reports/one-click-redesign/mixed-products-controlled.png','Manual Review',2),
           ('confirmed-shadow-control',ROOT/'data/one-click-calibration/shoe-shadow_loss.png','Fail',0),
           ('two-worn-shirts',ROOT/'reports/one-click-redesign/source-audit/8704d689-c081-4e59-b0ce-8151e1f661cf.png','Manual Review',2)]
    results=[]
    with (OUT/'chrome.log').open('w') as log:
        browser=subprocess.Popen(args,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(150):
                if (profile/'DevToolsActivePort').exists():break
                assert browser.poll() is None,'Chrome exited early'
                time.sleep(.1)
            port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
            with urllib.request.urlopen('http://127.0.0.1:'+port+'/json',timeout=10) as response: tabs=json.load(response)
            with connect(next(t['webSocketDebuggerUrl'] for t in tabs if t['type']=='page'),max_size=32*1024*1024) as socket:
                c=CDP(socket);c.call('Page.enable');c.call('Runtime.enable')
                c.call('Emulation.setDeviceMetricsOverride',{'width':1360,'height':1000,'deviceScaleFactor':1,'mobile':False})
                c.navigate(BASE+'/');c.screenshot(OUT/'desktop-upload.png')
                assert c.evaluate('!!document.querySelector("#one-click-upload")')
                assert not c.evaluate('!!document.querySelector("select[required]")')
                for name,path,status,count in cases:
                    start=time.monotonic()
                    if (OUT/name/'report.json').exists():
                        report=json.loads((OUT/name/'report.json').read_text())
                        identity=report['inspection_id'];c.navigate(BASE+'/inspection/'+identity)
                    else:
                        c.navigate(BASE+'/')
                        identity=c.evaluate('document.querySelector("input[name=submission_id]").value')
                        c.upload(path)
                        wait(c,'location.pathname === '+json.dumps('/inspection/'+identity)+' && !!document.querySelector(".verdict")')
                        report=export(identity,name)
                    assert report['status']==status,(name,report['status'])
                    assert len(report['segmentation']['products'])==count,(name,report['segmentation']['products'])
                    checks=report['checks'];by_id={x['identifier']:x for x in checks}
                    aggregate='Fail' if any(x['status']=='Fail' for x in checks) else 'Manual Review' if any(x['status']=='Manual Review' for x in checks) else 'Pass'
                    assert report['status']==aggregate
                    assert c.evaluate('document.querySelector(".verdict").textContent')==report['status']
                    assert c.evaluate('document.querySelectorAll(".product-head").length')==count
                    assert c.evaluate('document.querySelectorAll(".photo-panel svg a").length')==count
                    assert c.evaluate('document.body.innerText.includes("What we found") && document.body.innerText.includes("How to fix it")')
                    assert not c.evaluate('document.querySelector("details.details").open')
                    assert not c.evaluate('Array.from(document.querySelectorAll("pre")).some(p=>p.checkVisibility())')
                    explanation=get_json('/explanation/'+identity+'.json')
                    assert explanation['source']=='local' and explanation['reason']=='not_configured'
                    assert set(x['finding_id'] for x in explanation['items'])==set(by_id)
                    assert get_json('/report/'+identity+'.json')==report
                    if name=='confirmed-shadow-control':
                        assert by_id['photo.shadow_loss']['status']=='Fail'
                        assert by_id['photo.resolution']['status']=='Pass'
                        assert by_id['photo.coverage']['status']=='Manual Review'
                    if name=='mixed-products-controlled':
                        assert all(x['status']=='Pass' for x in checks if x['identifier'].startswith('photo.'))
                        assert by_id['product-1.blur']['reason_code']=='calibration_boundary'
                    if name=='two-worn-shirts':
                        assert by_id['photo.coverage']['status']=='Manual Review'
                        assert c.evaluate('!!document.querySelector("#coverage-question")')
                        assert all(any(x['identifier'].startswith('product-'+str(n)+'.') for x in checks) for n in [1,2])
                    c.screenshot(OUT/(name+'-desktop.png'))
                    c.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                    wait(c,'document.documentElement.scrollWidth <= innerWidth+1')
                    c.screenshot(OUT/(name+'-mobile.png'))
                    c.call('Emulation.setDeviceMetricsOverride',{'width':1360,'height':1000,'deviceScaleFactor':1,'mobile':False})
                    results.append({'case':name,'id':identity,'url':BASE+'/inspection/'+identity,'status':report['status'],
                        'products':count,'cpu_duration_ms':report['duration_ms'],'browser_verification_seconds':round(time.monotonic()-start,2),
                        'checks':[{k:x.get(k) for k in ['identifier','status','reason_code']} for x in checks],
                        'explanation_source':explanation['source'],'coverage':report['segmentation']['coverage']})
                    (OUT/'verification.json').write_text(json.dumps({'cases':results},indent=2),encoding='utf-8')
                    print(name,identity,status,count,report['duration_ms'],flush=True)
                    if name=='two-worn-shirts':
                        c.evaluate("document.querySelector('form[action=\"/confirm-coverage\"] button').click()")
                        wait(c,'location.pathname.startsWith("/inspection/") && location.pathname !== '+json.dumps('/inspection/'+identity))
                        child_id=c.evaluate('location.pathname.split("/").pop()')
                        child=export(child_id,'two-worn-shirts-confirmed')
                        assert child['segmentation']['whole_photo']==report['segmentation']['whole_photo']
                        assert [x for x in child['checks'] if x['identifier']!='photo.coverage']==[x for x in checks if x['identifier']!='photo.coverage']
                        assert next(x for x in child['checks'] if x['identifier']=='photo.coverage')['status']=='Pass'
                        assert get_json('/report/'+identity+'.json')==report
                        results.append({'case':'explicit-coverage-confirmation','id':child_id,'status':child['status'],
                            'parent_preserved':True,'all_noncoverage_findings_unchanged':True})
                        c.screenshot(OUT/'two-worn-shirts-confirmed-desktop.png')
                c.navigate(BASE+'/history');assert c.evaluate('document.body.innerText.includes("Saved inspections")')
                c.screenshot(OUT/'desktop-history.png')
                legacy=json.loads((ROOT/'reports/one-click-redesign/before.json').read_text())['existing_ids'][0]
                c.navigate(BASE+'/inspection/'+legacy)
                assert c.evaluate('document.body.innerText.includes("Photo result")')
        finally:
            browser.terminate();browser.wait(timeout=15)
    before=json.loads((ROOT/'reports/one-click-redesign/before.json').read_text())
    with sqlite3.connect(ROOT/'data/inspections.sqlite3') as db:
        identities={r[0] for r in db.execute('select id from inspections')}
    assert set(before['existing_ids'])<=identities
    for name,digest in before['preserved_sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    (OUT/'verification.json').write_text(json.dumps({'cases':results,'original_history_count':len(before['existing_ids']),
        'original_ids_preserved':True,'frozen_core_and_profiles_unchanged':True,'provider_live_verified':False,
        'provider_limit':'GROQ_API_KEY absent; actual local fallback and mocked provider contract verified',
        'mobile_overflow':False,'port':8505},indent=2),encoding='utf-8')


if __name__=='__main__':main()
