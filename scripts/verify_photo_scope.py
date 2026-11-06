"""Verify the current app through live HTTP and Chrome without changing historical reports."""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import urllib.request
from uuid import uuid4

from websockets.sync.client import connect

from inspector.auto_inspect import CheckResult, aggregate_required
from inspector.narration import approved_wording, validate
from scripts.verify_one_click import wait
from scripts.verify_submission import CDP

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/whole-photo-repair'


def run():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',action='store_true')
    parser.add_argument('--explanation',action='store_true')
    args=parser.parse_args()
    base='http://127.0.0.1:8505'
    target=OUT/'live';target.mkdir(exist_ok=True)
    rows=[]

    def read(path):
        with urllib.request.urlopen(base+path,timeout=90) as response:return response.read()

    def report(identity):return json.loads(read('/report/'+identity+'.json'))

    def submit(route,fields,path=None):
        boundary='framecheck-'+uuid4().hex
        identity=str(uuid4());fields={**fields,'submission_id':identity}
        parts=[f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode() for key,value in fields.items()]
        if path is not None:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{path.name}"\r\nContent-Type: image/png\r\n\r\n'.encode()+path.read_bytes()+b'\r\n')
        body=b''.join(parts)+f'--{boundary}--\r\n'.encode()
        connection=http.client.HTTPConnection('127.0.0.1',8505,timeout=90)
        try:
            connection.request('POST',route,body,{'Content-Type':'multipart/form-data; boundary='+boundary})
            response=connection.getresponse();message=response.read()
            assert response.status==303,(route,response.status,message[-500:])
        finally:connection.close()
        return report(identity)

    def check(name,result):
        assert result['pipeline_version']=='1.2.0-photo-scope-dip'
        assert result['status']==str(aggregate_required([CheckResult(**c) for c in result['checks']]))
        ids={c['identifier'] for c in result['checks']};assert len(ids)==len(result['checks'])
        rendered=read('/inspection/'+result['inspection_id']).decode()
        assert set(re.findall(r'data-finding-id="([^"]+)" data-statement',rendered))==ids
        assert all(not p['mask_verified'] for p in result['segmentation']['products'])
        approved=approved_wording(result);validate({'items':approved},approved)
        (target/(name+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
        rows.append({'case':name,'id':result['inspection_id'],'url':base+'/inspection/'+result['inspection_id'],
            'status':result['status'],'products':len(result['segmentation']['products']),
            'intended':[p['number'] for p in result['segmentation']['products'] if p['intended']],
            'required_non_pass':[(c['identifier'],c['status'],c.get('reason_code')) for c in result['checks'] if c['required'] and c['status']!='Pass']})
        print(name,result['status'],flush=True)

    def confirm(result,numbers):
        child=submit('/confirm-coverage',{'source_inspection_id':result['inspection_id'],'coverage_confirm':'yes',
            'intent_selection':'yes',**{f'intended_{n}':'yes' for n in numbers}})
        assert report(result['inspection_id'])==result
        return child

    shoe=ROOT/'reports/multi-product-debug/browser-verified/shoe/original.png'
    two=ROOT/'reports/multi-product-debug/browser-verified/confirmed-two-people/original.png'
    mixed=ROOT/'reports/one-click-redesign/mixed-products-controlled.png'
    cases=[('single-shoe',shoe,1),('two-people',two,4),('mixed-isolated',mixed,2),
        *[(name,OUT/(name+'.png'),count) for name,count in [('merged',1),('missed',1),('clipped',2),('corner',1),('small',1)]],
        ('dark-loss',ROOT/'data/one-click-calibration/shoe-shadow_loss.png',0)]
    parents={}
    for name,path,count in cases:
        result=submit('/inspect',{'one_click':'yes'},path);parents[name]=result
        assert len(result['segmentation']['products'])==count
        assert result['status']==('Fail' if name=='dark-loss' else 'Manual Review')
        check(name,result)
    accepted=confirm(parents['single-shoe'],[1]);assert accepted['status']=='Pass';check('single-shoe-confirmed',accepted)
    tops=confirm(parents['two-people'],[1,3]);assert tops['status']=='Manual Review'
    assert all(not c['required'] for c in tops['checks'] if c['identifier'].startswith(('product-2.','product-4.')))
    assert all(c['status']=='Pass' for c in tops['checks'] if c['identifier'].startswith('photo.group_'))
    check('two-intended-tops',tops)
    clipped=confirm(parents['clipped'],[1,2]);assert clipped['status']=='Manual Review'
    assert next(c for c in clipped['checks'] if c['identifier']=='product-2.mask_completeness')['status']=='Manual Review'
    check('clipped-confirmed',clipped)
    corner=confirm(parents['corner'],[1]);assert corner['status']=='Fail';check('corner-confirmed',corner)
    small=confirm(parents['small'],[1]);assert small['status']=='Fail'
    assert next(c for c in small['checks'] if c['identifier']=='photo.contrast')['status']=='Manual Review'
    check('small-confirmed',small)
    current=parents['merged']
    for points in [[[160,200],[409,200],[409,419],[160,419]],[[410,200],[659,200],[659,419],[410,419]]]:
        previous=current
        current=submit('/mark-product',{'source_inspection_id':previous['inspection_id'],'polygon':json.dumps(points)})
        assert report(previous['inspection_id'])==previous
    separated=confirm(current,[2,3]);assert separated['status']=='Pass';check('merged-marked-individually',separated)

    explanation={'requested':False}
    if args.explanation:
        response=json.loads(read('/explanation/'+accepted['inspection_id']+'.json'))
        validate({'items':response['items']},approved_wording(accepted))
        assert report(accepted['inspection_id'])==accepted
        explanation={key:response.get(key) for key in ('source','reason','usage')}
        explanation['requested']=True

    if args.browser:
        profile=ROOT/'.test-tmp'/('photo-scope-chrome-'+uuid4().hex);profile.mkdir()
        command=[r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new','--no-sandbox',
            '--disable-gpu','--in-process-gpu','--no-first-run','--no-default-browser-check',
            '--remote-debugging-port=0','--remote-allow-origins=*','--user-data-dir='+str(profile),'about:blank']
        with (target/'chrome.log').open('w') as log:
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
                    c.call('Emulation.setDeviceMetricsOverride',{'width':1360,'height':1000,'deviceScaleFactor':1,'mobile':False})
                    c.navigate(base+'/');c.upload(shoe)
                    wait(c,'!!document.querySelector(".verdict")')
                    assert c.evaluate('document.querySelector(".verdict").textContent')=='Manual Review'
                    c.evaluate('document.querySelector("#coverage-question form").requestSubmit(document.querySelector("#coverage-question button[type=submit]"))')
                    wait(c,'document.querySelector(".verdict")?.textContent === "Pass"')
                    c.screenshot(target/'single-product-desktop.png')
                    c.navigate(base+'/inspection/'+parents['two-people']['inspection_id'])
                    c.evaluate('document.querySelector("[name=intended_2]").checked=false;document.querySelector("[name=intended_4]").checked=false;document.querySelector("#coverage-question form").requestSubmit(document.querySelector("#coverage-question button[type=submit]"))')
                    wait(c,'!document.getElementById("coverage-question")')
                    identity=c.evaluate('location.pathname.split("/").pop()');browser_tops=report(identity)
                    assert [p['number'] for p in browser_tops['segmentation']['products'] if p['intended']]==[1,3]
                    assert c.evaluate('document.querySelectorAll(".product-head").length')==4
                    c.screenshot(target/'intended-tops-desktop.png')
                    c.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                    wait(c,'document.documentElement.scrollWidth<=innerWidth+1')
                    c.screenshot(target/'intended-tops-mobile.png')
                    c.navigate(base+'/inspection/'+parents['missed']['inspection_id'])
                    c.evaluate('document.getElementById("product-outline").closest("details").open=true')
                    c.evaluate('''(()=>{const svg=document.getElementById('product-outline');for(const xy of [[530,210],[729,210],[729,409],[530,409]]){const p=new DOMPoint(...xy).matrixTransform(svg.getScreenCTM());svg.dispatchEvent(new MouseEvent('click',{clientX:p.x,clientY:p.y,bubbles:true}));}document.getElementById('mark-product-form').requestSubmit();})()''')
                    wait(c,'document.querySelectorAll(".product-head").length===2')
                    identity=c.evaluate('location.pathname.split("/").pop()');marked=report(identity)
                    assert marked['segmentation']['source_inspection_id']==parents['missed']['inspection_id']
                    assert not marked['segmentation']['coverage']['intended_complete']
                    assert marked['status']=='Manual Review'
                    wait(c,'document.documentElement.scrollWidth<=innerWidth+1')
                    c.screenshot(target/'marked-missing-mobile.png')
            finally:
                browser.terminate();browser.wait(timeout=15)

    before=json.loads((OUT/'before.json').read_text())
    with sqlite3.connect('file:data/inspections.sqlite3?mode=ro',uri=True) as db:
        saved=dict(db.execute('select id,result_json from inspections'))
    for identity,digest in before['reports'].items():assert hashlib.sha256(saved[identity].encode()).hexdigest()==digest
    assert hashlib.sha256((ROOT/'data/one-click-policy.json').read_bytes()).hexdigest()==before['files']['data/one-click-policy.json']
    assert hashlib.sha256((ROOT/'inspector/models.py').read_bytes()).hexdigest()==before['files']['inspector/models.py']
    summary={'cases':rows,'browser_verified':args.browser,'historical_reports_preserved':len(before['reports']),
        'blur_policy_unchanged':True,'legacy_model_unchanged':True,'explanation':explanation}
    (target/'verification.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':run()
