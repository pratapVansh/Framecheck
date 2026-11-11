"""Verify 1.7 controls and native photos on an isolated HTTP server in desktop/mobile Chrome."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from uuid import uuid4

from websockets.sync.client import connect

from inspector.auto_inspect import PIPELINE_VERSION
from inspector.web import make_server
from scripts.verify_one_click import wait
from scripts.verify_submission import CDP

ROOT=Path(__file__).resolve().parents[1]


def run():
    sys.path.insert(0,str(ROOT/'tests'))
    from test_photo_scope import audit_image
    out=ROOT/'reports/audit-improvements';out.mkdir(parents=True,exist_ok=True)
    if not (out/'before.json').is_file():
        with sqlite3.connect('file:'+(ROOT/'data/inspections.sqlite3').as_posix()+'?mode=ro',uri=True) as db:
            baseline=dict(db.execute('select id,result_json from inspections'))
        frozen=['inspector/models.py','inspector/segmentation.py','inspector/pipeline.py','inspector/images.py']
        snapshot={'reports':{identity:hashlib.sha256(raw.encode()).hexdigest() for identity,raw in baseline.items()},
            'policy_sha256':hashlib.sha256((ROOT/'data/one-click-policy.json').read_bytes()).hexdigest(),
            'frozen':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in frozen}}
        (out/'before.json').write_text(json.dumps(snapshot,indent=2),encoding='utf-8')
    cases=[('single',ROOT/'reports/automatic-coverage/single.png','Pass',1),
           ('multi',ROOT/'reports/automatic-coverage/multi.png','Needs Review',2)]
    for name,state in [('merged','Needs Review'),('corner','Needs Improvement'),('small','Needs Improvement')]:
        path=out/(name+'.png');path.write_bytes(audit_image(name));cases.append((name,path,state,1))
    assert all(path.is_file() for _,path,_,_ in cases),'Local private native-photo fixtures are required.'
    server=make_server(0,out/('browser-'+uuid4().hex+'.sqlite3'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    profile=ROOT/'.test-tmp'/('audit-improvements-chrome-'+uuid4().hex);profile.mkdir(parents=True)
    command=[r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new','--no-sandbox',
        '--disable-gpu','--in-process-gpu','--disable-background-networking','--no-first-run',
        '--no-default-browser-check','--remote-debugging-port=0','--remote-allow-origins=*',
        '--user-data-dir='+str(profile),'about:blank']
    originals={};rows=[];browser=None
    def read(path):
        with urllib.request.urlopen(base+path,timeout=15) as response:return response.read()
    def report(identity):return json.loads(read('/report/'+identity+'.json'))
    def ready(c,identity):wait(c,'location.pathname==='+json.dumps('/inspection/'+identity)+' && !!document.querySelector(".primary-suggestion")')
    try:
        with (out/'chrome.log').open('w') as log:
            browser=subprocess.Popen(command,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            for _ in range(100):
                if (profile/'DevToolsActivePort').exists():break
                assert browser.poll() is None,'Chrome exited before debugger startup'
                time.sleep(.1)
            port=(profile/'DevToolsActivePort').read_text().splitlines()[0]
            with urllib.request.urlopen('http://127.0.0.1:'+port+'/json',timeout=10) as response:tabs=json.load(response)
            with connect(next(t['webSocketDebuggerUrl'] for t in tabs if t['type']=='page'),max_size=32*1024*1024) as socket:
                c=CDP(socket);c.call('Page.enable');c.call('Runtime.enable');c.call('Network.enable')
                c.call('Network.setBlockedURLs',{'urls':['*/explanation/*']})
                for name,path,state,count in cases:
                    c.call('Emulation.setDeviceMetricsOverride',{'width':1360,'height':1000,'deviceScaleFactor':1,'mobile':False})
                    c.navigate(base+'/')
                    identity=c.evaluate('document.querySelector("[name=submission_id]").value')
                    c.upload(path);ready(c,identity)
                    result=report(identity);meta=result['segmentation'];by_id={x['identifier']:x for x in result['checks']}
                    assert result['pipeline_version']==PIPELINE_VERSION and result['status']==state,(name,result['status'])
                    assert meta['product_count']==count
                    originals[identity]=read('/report/'+identity+'.json')
                    if name=='single':assert result['quality_score']['score']==100
                    if name=='multi':
                        assert meta['incidental_count']==2 and [p['number'] for p in meta['products']]==[1,2]
                        assert result['quality_score']['score']==78
                        assert result['quality_score']['incomplete_checks']==['photo.blur','product-1.blur','product-3.blur']
                    if name=='merged':assert by_id['photo.coverage']['reason_code']=='item_count_unresolved'
                    if name=='corner':assert result['quality_score']['score']==90 and len(result['quality_score']['critical_failures'])==3
                    if name=='small':
                        assert by_id['photo.blur']['measurement']['assessment_scope']=='included_products'
                        assert by_id['photo.blur']['measurement']['whole_photo_observation']['status']=='Manual Review'
                        assert result['quality_score']['uncertainty_withheld']==8
                    for width,height in [(1360,1000),(390,844)]:
                        c.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':width==390})
                        wait(c,'document.documentElement.scrollWidth<=innerWidth+1')
                        assert c.evaluate('document.querySelector(".primary-suggestion a").getBoundingClientRect().bottom<innerHeight')
                        assert c.evaluate('document.querySelectorAll("[data-intended-product]").length')==count
                        assert c.evaluate('document.querySelectorAll("[data-finding-id][data-statement]").length')==len(result['checks'])
                        assert c.evaluate('!document.querySelector("#inspection-details").open')
                        c.screenshot(out/(name+'-'+str(width)+'.png'))
                    if name=='multi':
                        assert c.evaluate('document.querySelector(".primary-suggestion a").getAttribute("href")')==f'/evidence/{identity}/original.png'
                    if name=='merged':
                        c.evaluate('document.querySelector(".primary-suggestion a").click()')
                        wait(c,'document.querySelector("#inspection-details").open && document.querySelector("#coverage-question").open')
                        # Mark both visible controls using native SVG clicks; each submit creates an immutable child.
                        for left,right in [(160,409),(410,659)]:
                            child=c.evaluate('document.querySelector("#mark-product-form [name=submission_id]").value')
                            points=[[left,200],[right,200],[right,419],[left,419]]
                            c.evaluate('''(()=>{const svg=document.querySelector('#product-outline');
                              for(let node=svg;node;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;
                              svg.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
                              if(!document.querySelector('#marking-status').textContent.startsWith('1 '))throw Error('Keyboard marking failed');
                              document.querySelector('#outline-clear').click();
                              for(const [x,y] of '''+json.dumps(points)+'''){
                                const point=new DOMPoint(x,y).matrixTransform(svg.getScreenCTM());
                                svg.dispatchEvent(new MouseEvent('click',{clientX:point.x,clientY:point.y,bubbles:true}));
                              }document.querySelector('#mark-product-form').requestSubmit();})()''')
                            ready(c,child);originals[child]=read('/report/'+child+'.json')
                        child=c.evaluate('document.querySelector("#coverage-question form [name=submission_id]").value')
                        c.evaluate('''document.querySelector('#inspection-details').open=true;
                          document.querySelector('#coverage-question').open=true;
                          document.querySelector('[name=intended_1]').checked=false;
                          document.querySelector('[name=intended_2]').checked=true;
                          document.querySelector('[name=intended_3]').checked=true;
                          document.querySelector('#coverage-question form').requestSubmit(document.querySelector('#coverage-question button[type=submit]'));''')
                        ready(c,child);recovered=report(child)
                        assert recovered['status']=='Pass' and recovered['segmentation']['product_count']==2
                        assert [p['region_id'] for p in recovered['segmentation']['products']]==[2,3]
                        assert [p['number'] for p in recovered['segmentation']['products']]==[1,2]
                        assert all(not p['mask_verified'] for p in recovered['segmentation']['products'])
                        (out/'recovered.json').write_text(json.dumps(recovered,indent=2),encoding='utf-8')
                        c.screenshot(out/'recovered-mobile.png')
                    (out/(name+'-browser.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
                    rows.append({'case':name,'status':state,'products':count,'score':result['quality_score']['score']})
                    print(json.dumps(rows[-1]),flush=True)
                # Re-upload identical native pixels through the browser; localization is reused, scores are recomputed.
                c.navigate(base+'/');identity=c.evaluate('document.querySelector("[name=submission_id]").value')
                c.upload(cases[1][1]);ready(c,identity);cached=report(identity)
                assert cached['segmentation']['timings']['localization_cache_hit']
                assert cached['quality_score']['score']==78 and cached['status']=='Needs Review'
                for identity,raw in originals.items():assert read('/report/'+identity+'.json')==raw
    finally:
        if browser is not None:browser.terminate();browser.wait(timeout=15)
        server.shutdown();thread.join(timeout=5);server.server_close()
    before=json.loads((out/'before.json').read_text())
    with sqlite3.connect('file:'+(ROOT/'data/inspections.sqlite3').as_posix()+'?mode=ro',uri=True) as db:
        current=dict(db.execute('select id,result_json from inspections'))
    assert all(hashlib.sha256(current[i].encode()).hexdigest()==digest for i,digest in before['reports'].items())
    assert hashlib.sha256((ROOT/'data/one-click-policy.json').read_bytes()).hexdigest()==before['policy_sha256']
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest for path,digest in before['frozen'].items())
    summary={'cases':rows,'desktop_mobile_verified':[1360,390],'primary_action_visible':True,
        'count_recovery_and_keyboard_marking_verified':True,'cache_reupload_verified':True,
        'historical_reports_preserved':len(before['reports']),'policy_and_frozen_core_preserved':True,
        'paid_groq_calls':0,'verification_database':str(server.database)}
    (out/'browser-verification.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':run()
