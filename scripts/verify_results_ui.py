"""Verify the live upload/results UI and preserve saved reports by JSON hash.

Uses the private two-shirt photo and a deliberate crop of its dominant red shirt.
Chrome runs headless; automatic Groq requests are blocked to avoid repeated calls.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request
from uuid import uuid4

from PIL import Image
from websockets.sync.client import connect

from inspector.auto_inspect import PIPELINE_VERSION
from scripts.verify_one_click import wait
from scripts.verify_submission import CDP

ROOT=Path(__file__).resolve().parents[1]


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://127.0.0.1:8505')
    parser.add_argument('--database',type=Path,default=ROOT/'data/inspections.sqlite3')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/results-redesign')
    args=parser.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'reports/multi-product-debug/d041db85-0eb8-4f7a-98a5-dcbb6135c94d/original.png'
    cropped=out/'dominant-cropped.png'
    with Image.open(source) as image:image.crop((300,300,770,880)).save(cropped)
    snapshot=json.loads((out/'before.json').read_text())
    profile=ROOT/'.test-tmp'/('results-chrome-'+uuid4().hex);profile.mkdir(parents=True)
    command=[r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new','--no-sandbox',
        '--disable-gpu','--in-process-gpu','--no-first-run','--no-default-browser-check',
        '--disable-background-networking','--remote-debugging-port=0','--remote-allow-origins=*',
        '--user-data-dir='+str(profile),'about:blank']
    def read(path):
        with urllib.request.urlopen(args.base+path,timeout=15) as response:return response.read()
    def report(identity):return json.loads(read('/report/'+identity+'.json'))
    rows=[]
    with (out/'chrome.log').open('w') as log:
        browser=subprocess.Popen(command,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(100):
                if (profile/'DevToolsActivePort').exists():break
                assert browser.poll() is None,'Chrome exited before debugger startup'
                time.sleep(.1)
            port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
            with urllib.request.urlopen('http://127.0.0.1:'+port+'/json',timeout=10) as response:tabs=json.load(response)
            with connect(next(t['webSocketDebuggerUrl'] for t in tabs if t['type']=='page'),max_size=32*1024*1024) as socket:
                c=CDP(socket);c.call('Page.enable');c.call('Runtime.enable');c.call('Network.enable')
                c.call('Network.setBlockedURLs',{'urls':['*/explanation/*']})
                for name,path in [('two-shirts',source),('dominant-cropped',cropped)]:
                    c.call('Emulation.setDeviceMetricsOverride',{'width':1360,'height':1000,'deviceScaleFactor':1,'mobile':False})
                    c.navigate(args.base+'/');c.upload(path)
                    wait(c,'!!document.querySelector("[data-product-count]")')
                    identity=c.evaluate('location.pathname.split("/").pop()');value=report(identity)
                    assert value['pipeline_version']==PIPELINE_VERSION
                    meta=value['segmentation'];score=value['quality_score']
                    assert meta['report_schema_version']==2
                    assert c.evaluate('document.querySelectorAll("[data-intended-product]").length')==len(meta['products'])
                    assert c.evaluate('document.querySelectorAll("[data-product-number]").length')==len(meta['products'])
                    assert c.evaluate('!document.querySelector("#inspection-details").open')
                    assert c.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    assert c.evaluate('document.querySelector(".review-photo").getBoundingClientRect().top<300')
                    if name=='two-shirts':
                        assert meta['product_count']==2 and meta['incidental_count']==2
                        assert [p['number'] for p in meta['products']]==[1,2]
                        assert [p['region_id'] for p in meta['products']]==[1,3]
                        assert all(p['label']=='Top' for p in meta['products'])
                        assert all(p['label']=='Trousers' and p['number'] is None for p in meta['incidental_regions'])
                        assert c.evaluate('!document.querySelector("#incidental-context").checkVisibility()')
                        edge=next(x for x in score['components'] if x['component']=='product.edge')
                        assert edge['status']=='Pass'
                    else:
                        assert meta['product_count']>=1
                        assert any(check['required'] and check.get('reason_code')=='possible_cropping' for check in value['checks'])
                        assert next(x for x in score['components'] if x['component']=='product.edge')['withheld']==5
                    c.screenshot(out/(name+'-desktop.png'))
                    c.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                    wait(c,'document.documentElement.scrollWidth<=innerWidth+1')
                    c.screenshot(out/(name+'-mobile.png'))
                    # Evidence links must open the containing Details and reach the exact check.
                    c.evaluate('document.querySelector(".product-card a").click()')
                    wait(c,'document.querySelector("#inspection-details").open')
                    assert c.evaluate('document.querySelector(".product-card a").hash.slice(1)') in {f'evidence-{x["identifier"]}' for x in value['checks']}
                    (out/(name+'.json')).write_text(json.dumps(value,indent=2),encoding='utf-8')
                    (out/(name+'.html')).write_bytes(read('/inspection/'+identity))
                    rows.append({'case':name,'url':args.base+'/inspection/'+identity,'id':identity,
                        'products':meta['product_count'],'incidental':meta['incidental_count'],
                        'score':score['score'],'verdict':score['verdict'],'assessed_coverage_percent':score['assessed_coverage_percent']})
                    print(json.dumps(rows[-1]),flush=True)
                    if name=='two-shirts':
                        raw=read('/report/'+identity+'.json')
                        c.evaluate('document.querySelector("#coverage-question").open=true;document.querySelector("[name=intended_2]").checked=true;document.querySelector("#coverage-question form").requestSubmit(document.querySelector("#coverage-question button[type=submit]"))')
                        wait(c,'location.pathname.split("/").pop()!=='+json.dumps(identity)+' && !!document.querySelector("[data-product-count]")')
                        child=report(c.evaluate('location.pathname.split("/").pop()'))
                        assert child['segmentation']['product_count']==3
                        assert [p['number'] for p in child['segmentation']['products']]==[1,2,3]
                        assert child['segmentation']['products'][1]['region_id']==2
                        assert read('/report/'+identity+'.json')==raw
                        assert next(x for x in child['quality_score']['components'] if x['component']=='product.edge')['withheld']==5
                        (out/'override.json').write_text(json.dumps(child,indent=2),encoding='utf-8')
        finally:
            browser.terminate();browser.wait(timeout=15)
    with sqlite3.connect('file:'+args.database.as_posix()+'?mode=ro',uri=True) as db:
        current=dict(db.execute('select id,result_json from inspections'))
    for identity,digest in snapshot['reports'].items():assert hashlib.sha256(current[identity].encode()).hexdigest()==digest
    assert hashlib.sha256((ROOT/'data/one-click-policy.json').read_bytes()).hexdigest()==snapshot['policy_sha256']
    summary={'cases':rows,'browser_verified':True,'historical_reports_preserved':len(snapshot['reports']),
        'viewport_widths':[1360,390],'override_verified':True,'groq_requests_blocked':True}
    (out/'verification.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':run()
