"""Focused Chrome uploads against updated handlers and a separate temporary inspection database."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
from uuid import uuid4

from websockets.sync.client import connect

from inspector import narration
from inspector.models import CheckResult,aggregate
from inspector.web import make_server
from scripts.verify_submission import CDP
from scripts.verify_one_click import wait

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/multi-product-debug/browser-verified'


def run():
    OUT.mkdir(parents=True,exist_ok=False)
    # Keep this verification local and token-free; the user's server/environment is a separate process.
    os.environ.pop('GROQ_API_KEY',None)
    narration.ENV_FILE=OUT/'disabled.env'
    server=make_server(0,OUT/'verification.sqlite3')
    server.RequestHandlerClass.log_message=lambda *args:None
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def get(path):
        with urllib.request.urlopen(base+path,timeout=10) as response:return json.load(response)
    profile=ROOT/'.test-tmp'/('multi-product-chrome-'+uuid4().hex);profile.mkdir()
    args=[r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new','--no-sandbox',
          '--disable-gpu','--in-process-gpu','--no-first-run','--no-default-browser-check',
          '--remote-debugging-port=0','--remote-allow-origins=*','--user-data-dir='+str(profile),'about:blank']
    source=ROOT/'reports/multi-product-debug'
    cases=[('shoe',source/'474ac439-9de4-4ec5-b58d-301d6a753d1b/original.png',1,True,'Pass'),
           ('confirmed-two-people',source/'d041db85-0eb8-4f7a-98a5-dcbb6135c94d/original.png',4,True,'Manual Review'),
           ('single-person',source/'e6d143f8-5bd8-4a30-86e7-8cd9fc7d5615/original.png',2,True,'Manual Review'),
           ('mixed-isolated',ROOT/'reports/one-click-redesign/mixed-products-controlled.png',2,True,'Manual Review'),
           ('mixed-people-footwear',source/'people-and-footwear-controlled.png',5,False,'Manual Review'),
           ('missing-region',ROOT/'data/public-shirts/originals/T_Shirt_Blue.jpg',0,False,'Manual Review'),
           ('known-dark-loss',ROOT/'data/one-click-calibration/shoe-shadow_loss.png',0,False,'Fail')]
    results=[]
    with (OUT/'chrome.log').open('w') as log:
        browser=subprocess.Popen(args,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(120):
                if (profile/'DevToolsActivePort').exists():break
                assert browser.poll() is None
                time.sleep(.1)
            port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
            with urllib.request.urlopen('http://127.0.0.1:'+port+'/json',timeout=10) as response:tabs=json.load(response)
            with connect(next(t['webSocketDebuggerUrl'] for t in tabs if t['type']=='page'),max_size=32*1024*1024) as socket:
                c=CDP(socket);c.call('Page.enable');c.call('Runtime.enable')
                c.call('Emulation.setDeviceMetricsOverride',{'width':1360,'height':1000,'deviceScaleFactor':1,'mobile':False})
                for name,path,count,complete,expected in cases:
                    c.navigate(base+'/');identity=c.evaluate('document.querySelector("input[name=submission_id]").value')
                    c.upload(path)
                    wait(c,'location.pathname === '+json.dumps('/inspection/'+identity)+' && !!document.querySelector(".verdict")')
                    report=get('/report/'+identity+'.json')
                    products=report['segmentation']['products'];coverage=report['segmentation']['coverage']
                    assert len(products)==count and coverage['coverage_complete']==complete,(name,products,coverage)
                    assert report['status']==expected==str(aggregate([CheckResult(**check) for check in report['checks']]))
                    assert report['pipeline_version']=='1.1.0-multi-product-dip'
                    assert c.evaluate('document.querySelectorAll(".product-head").length')==count
                    assert c.evaluate('document.querySelectorAll(".photo-panel svg a").length')==count
                    assert c.evaluate("!!document.querySelector('[data-statement][data-finding-id=\"photo.coverage\"]')")
                    assert c.evaluate('!!document.querySelector("#coverage-question")')==(not complete)
                    assert c.evaluate('document.querySelector(".verdict").textContent')==expected
                    for p in products:
                        assert c.evaluate('!!document.getElementById('+json.dumps('product-'+str(p['number']))+')')
                    response=get('/explanation/'+identity+'.json')
                    assert response['source']=='local' and response['reason']=='not_configured'
                    narration.validate({'items':response['items']},narration.approved_wording(report))
                    assert get('/report/'+identity+'.json')==report
                    folder=OUT/name;folder.mkdir()
                    (folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                    for kind in ['original','mask','overlay']:
                        with urllib.request.urlopen(f'{base}/evidence/{identity}/{kind}.png',timeout=10) as response:
                            (folder/(kind+'.png')).write_bytes(response.read())
                    c.screenshot(folder/'desktop.png')
                    c.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                    wait(c,'document.documentElement.scrollWidth<=innerWidth+1')
                    c.screenshot(folder/'mobile.png')
                    c.call('Emulation.setDeviceMetricsOverride',{'width':1360,'height':1000,'deviceScaleFactor':1,'mobile':False})
                    if name=='confirmed-two-people':
                        assert [p.get('label') for p in products]==['Top','Trousers','Top','Trousers']
                        prior=json.loads((source/'d041db85-0eb8-4f7a-98a5-dcbb6135c94d/before-report.json').read_text())
                        old={check['identifier']:check for check in prior['checks']}
                        new={check['identifier']:check for check in report['checks']}
                        assert report['segmentation']['whole_photo']==prior['segmentation']['whole_photo']
                        for old_n,new_n in [(1,1),(2,3)]:
                            for metric in ['blur','noise']:
                                assert old[f'product-{old_n}.{metric}']['measurement']==new[f'product-{new_n}.{metric}']['measurement']
                        assert new['photo.coverage']['status']=='Pass'
                        assert new['product-2.edge']['reason_code']==new['product-4.edge']['reason_code']=='possible_cropping'
                    results.append({'case':name,'products':count,'coverage_complete':complete,'verdict':expected,
                        'reviews':[check['identifier'] for check in report['checks'] if check['status']=='Manual Review'],
                        'findings_visible':True,'mobile_overflow':False,'grounding_validated':True,'duration_ms':report['duration_ms']})
                    (OUT/'verification.json').write_text(json.dumps({'cases':results,'generation_requests':0,
                        'database_isolated':True,'production_server_untouched':True},indent=2),encoding='utf-8')
                    print(name,count,expected,flush=True)
        finally:
            browser.terminate();browser.wait(timeout=15)
            server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':run()
