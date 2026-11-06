"""Final HTTP, real Chrome, evidence, keyboard/mobile, and restart checks."""
import base64
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request
from uuid import uuid4

from websockets.sync.client import connect

from inspector.benchmark import read_manifest, image_bytes
from inspector.images import decode_image
from inspector.profiles import load_profile
from inspector.web import FORM_FIELDS, render_result
import scripts.verify_final_app as http


class CDP:
    def __init__(self, socket):
        self.socket=socket;self.identity=0

    def call(self, method, params=None):
        self.identity+=1;identity=self.identity
        self.socket.send(json.dumps({'id':identity,'method':method,'params':params or {}}))
        while True:
            message=json.loads(self.socket.recv(timeout=15))
            if message.get('id')==identity:
                if 'error' in message:raise RuntimeError(message['error'])
                return message.get('result',{})

    def evaluate(self, expression):
        result=self.call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
        if 'exceptionDetails' in result:raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')

    def wait(self, expression):
        until=time.monotonic()+12
        while time.monotonic()<until:
            try:
                if self.evaluate(expression):return
            except RuntimeError:
                pass
            time.sleep(.1)
        raise AssertionError('Browser condition timed out: '+expression)

    def navigate(self, url):
        self.call('Page.navigate',{'url':url})
        self.wait('location.href === '+json.dumps(url)+' && document.readyState === "complete"')

    def screenshot(self, path):
        path.write_bytes(base64.b64decode(self.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})['data']))

    def upload(self, path):
        doc=self.call('DOM.getDocument')['root']['nodeId']
        node=self.call('DOM.querySelector',{'nodeId':doc,'selector':'#image'})['nodeId']
        self.call('DOM.setFileInputFiles',{'nodeId':node,'files':[str(path.resolve())]})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('reports/revision-phase7/verified-run'))
    args=parser.parse_args()
    root=Path('.').resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);target=out/'verification.json'
    protected=[Path('data/inspections.sqlite3'),Path('data/revision-v2/benchmark-v2.2.jsonl'),Path('data/calibrated-profile.json')]
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    old=read_manifest('data/manifest.jsonl');source={r['id']:r for r in old}
    test=[r for r in read_manifest('data/revision-v2/benchmark-v2.2.jsonl') if r['split']=='test']
    low=next(r for r in test if r['kind']=='synthetic' and r['transforms'][0]['defect']=='low_resolution')
    cases=[(source['38954'],'isolated_product','Demo White Background','Pass'),(source['5003'],'isolated_product','Demo White Background','Fail'),
           (source['22608'],'isolated_product','Demo White Background','Manual Review'),(source['44892'],'model_worn','Demo White Background','Manual Review'),
           (source['44892'],'uncertain','Demo White Background','Manual Review'),(low,low['photo_context'],'quality-v3-profile','Fail')]
    profiles={'Demo White Background':__import__('inspector.models',fromlist=['InspectionProfile']).InspectionProfile(),
              'quality-v3-profile':load_profile(Path('data/quality-v3-profile.json'))[0]}
    for name,isolated_status in [('shoe-96-original.png','Manual Review'),('shoe-96-gaussian.png','Fail'),('shoe-1254-original.png','Manual Review')]:
        for context in ('isolated_product','model_worn','uncertain'):
            cases.append(({'image':str(Path('data/quality-v3/regression')/name)},context,'quality-v3-profile',isolated_status if context=='isolated_product' else 'Manual Review'))
    database=out/'app-check.sqlite3';saved=[];screens=[];focus=[]
    profile_dir=root/'.test-tmp'/('submission-chrome-'+uuid4().hex);profile_dir.mkdir(exist_ok=False)
    chrome=r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    args=[chrome,'--headless=new','--no-sandbox','--disable-gpu-sandbox','--in-process-gpu','--disable-gpu','--disable-background-networking',
          '--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--remote-allow-origins=*','--user-data-dir='+str(profile_dir),'about:blank']
    with (out/'chrome.log').open('w') as errors:
        browser=subprocess.Popen(args,stdout=errors,stderr=errors,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(50):
                if (profile_dir/'DevToolsActivePort').exists():break
                assert browser.poll() is None,'Chrome exited before debugging was ready'
                time.sleep(.1)
            debug_port=(profile_dir/'DevToolsActivePort').read_text().splitlines()[0]
            with urllib.request.urlopen('http://127.0.0.1:'+debug_port+'/json',timeout=10) as response:pages=json.load(response)
            with connect(next(p['webSocketDebuggerUrl'] for p in pages if p['type']=='page'),open_timeout=10,max_size=16*1024*1024) as socket:
                c=CDP(socket);c.call('Page.enable');c.call('Runtime.enable')
                version=c.call('Browser.getVersion')
                with http.server(database,out/'http.log') as (port,pid):
                    base='http://127.0.0.1:'+str(port)
                    c.navigate(base+'/');c.wait('document.querySelector("#image") !== null')
                    assert c.evaluate('document.querySelector("input[name=profile]").value')=='quality-v3-profile'
                    for row,context,profile,expected in cases:
                        identity=str(uuid4());fields={'submission_id':identity,'photo_context':context,'profile':profile}
                        fields.update({k:str(getattr(profiles[profile],k)) for k in FORM_FIELDS})
                        body,headers=http.multipart(fields,image_bytes(row))
                        for _ in range(2):assert http.request(port,'POST','/inspect',body,headers)[0]==303
                        code,_,raw=http.request(port,'GET','/report/'+identity+'.json');result=json.loads(raw)
                        assert code==200 and result['status']==expected and len(result['checks'])==12
                        assert result['profile']==profiles[profile].to_dict()
                        evidence={}
                        for kind in ('original','mask','overlay'):
                            code,_,blob=http.request(port,'GET',f'/evidence/{identity}/{kind}.png');rgb,_=decode_image(blob)
                            assert code==200 and rgb.shape[:2]==(result['height'],result['width'])
                            evidence[kind]=hashlib.sha256(blob).hexdigest()
                        saved.append({'id':identity,'status':expected,'json_sha256':hashlib.sha256(raw).hexdigest(),'evidence_sha256':evidence})
                    c.call('Emulation.setDeviceMetricsOverride',{'width':1280,'height':900,'deviceScaleFactor':1,'mobile':False})
                    c.navigate(base+'/?profile=Demo%20White%20Background');c.wait('document.querySelector("#image") !== null')
                    assert not c.evaluate('document.querySelector("form").checkValidity()')
                    assert c.evaluate('document.querySelector("#photo_context").value')==''
                    for _ in range(14):
                        c.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Tab','code':'Tab','windowsVirtualKeyCode':9})
                        c.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Tab','code':'Tab','windowsVirtualKeyCode':9})
                        item=c.evaluate('({tag:document.activeElement.tagName,id:document.activeElement.id,outline:getComputedStyle(document.activeElement).outlineStyle})');focus.append(item)
                        if item['tag']=='BUTTON':break
                    assert any(x['id']=='image' for x in focus) and any(x['id']=='photo_context' for x in focus)
                    assert focus[-1]['tag']=='BUTTON' and focus[-1]['outline']!='none'
                    c.screenshot(out/'desktop-home.png');screens.append('desktop-home.png')
                    c.upload(Path(source['38954']['image']))
                    c.evaluate('document.querySelector("#photo_context").value="isolated_product"; document.querySelector("button[type=submit]").focus()')
                    state=c.evaluate('({valid:document.querySelector("form").checkValidity(),focus:document.activeElement.tagName,invalid:Array.from(document.querySelectorAll("input,select")).filter(e=>!e.validity.valid).map(e=>({id:e.id,value:e.value,message:e.validationMessage}))})')
                    (out/'form-before-submit.json').write_text(json.dumps(state,indent=2))
                    assert state['valid'], state
                    c.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Enter','code':'Enter','text':'\r','unmodifiedText':'\r','windowsVirtualKeyCode':13,'nativeVirtualKeyCode':13})
                    c.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Enter','code':'Enter','windowsVirtualKeyCode':13})
                    c.wait('location.pathname.startsWith("/inspection/") && document.body.innerText.includes("Photo result")')
                    c.wait('Array.from(document.images).every(i=>i.complete && i.naturalWidth>0)')
                    browser_identity=c.evaluate('location.pathname.split("/").pop()')
                    assert c.evaluate('document.body.innerText.includes("Pass")')
                    c.screenshot(out/'desktop-result.png');screens.append('desktop-result.png')
                    assert c.evaluate('(()=>{let i=document.querySelector(".result-photo"),r=i.getBoundingClientRect();return i.src.endsWith("/original.png") && r.width<=i.naturalWidth && r.height<=i.naturalHeight})()')
                    c.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                    c.evaluate('Array.from(document.querySelectorAll("summary")).find(s=>s.textContent.includes("View overlay and mask")).click()')
                    c.wait('document.documentElement.scrollWidth <= innerWidth+1')
                    c.screenshot(out/'mobile-result.png');screens.append('mobile-result.png')
                    c.navigate(base+'/history');c.wait('document.body.innerText.includes("Saved photos")')
                    assert browser_identity in c.evaluate('document.body.innerHTML')
                    c.screenshot(out/'mobile-history.png');screens.append('mobile-history.png')
                    c.navigate(base+'/?profile=quality-v3-profile');c.wait('document.querySelector("#image") !== null')
                    assert c.evaluate('document.body.innerText.includes("did not meet validation targets")')
                    c.upload(Path(source['38954']['image']))
                    c.evaluate('document.querySelector("#photo_context").value="isolated_product"; document.querySelector("#min_coverage").value="0.95";document.querySelector("#max_coverage").value="0.4";document.querySelector("form").requestSubmit()')
                    c.wait('document.querySelector("[role=alert]") !== null')
                    assert c.evaluate('document.querySelector("input[name=profile]").value')=='quality-v3-profile'
                    assert c.evaluate('document.querySelector("#photo_context").value')=='isolated_product'
                    c.screenshot(out/'mobile-validation-error.png');screens.append('mobile-validation-error.png')
                    c.navigate(base+'/?profile=quality-v3-profile');c.wait('document.querySelector("#image") !== null')
                    assert c.evaluate('document.querySelector("#max_noise").step')=='any'
                    c.upload(Path(source['38954']['image']))
                    c.evaluate('document.querySelector("#photo_context").value="isolated_product";document.querySelector("form").requestSubmit()')
                    c.wait('location.pathname.startsWith("/inspection/") && document.body.innerText.includes("Photo result")')
                    browser_cal=c.evaluate('location.pathname.split("/").pop()')
                    code,_,raw=http.request(port,'GET','/report/'+browser_cal+'.json');assert code==200 and json.loads(raw)['profile']['calibrated']
                    body,headers=http.multipart({'submission_id':str(uuid4()),'profile':'Demo White Background','photo_context':'isolated_product'},b'not an image')
                    assert http.request(port,'POST','/inspect',body,headers)[0]==400
                    first_pid=pid
                with http.server(database,out/'http.log') as (port,pid):
                    assert pid!=first_pid
                    for item in saved:
                        code,_,raw=http.request(port,'GET','/report/'+item['id']+'.json');assert code==200 and hashlib.sha256(raw).hexdigest()==item['json_sha256']
                        for kind,digest in item['evidence_sha256'].items():
                            code,_,blob=http.request(port,'GET',f'/evidence/{item["id"]}/{kind}.png');assert code==200 and hashlib.sha256(blob).hexdigest()==digest
                    c.navigate(f'http://127.0.0.1:{port}/inspection/{browser_identity}');c.wait('document.body.innerText.includes("Photo result")')
                    for label,item in zip(('shoe-original','shoe-gaussian','shoe-large'),saved[6::3]):
                        c.call('Emulation.setDeviceMetricsOverride',{'width':1280,'height':900,'deviceScaleFactor':1,'mobile':False})
                        c.navigate(f'http://127.0.0.1:{port}/inspection/{item["id"]}')
                        c.wait('Array.from(document.images).every(i=>i.complete && i.naturalWidth>0)')
                        assert c.evaluate('(()=>{let i=document.querySelector(".result-photo"),r=i.getBoundingClientRect();return i.src.endsWith("/original.png") && r.width<=i.naturalWidth && r.height<=i.naturalHeight})()')
                        c.screenshot(out/(label+'.png'));screens.append(label+'.png')
                        c.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
                        assert c.evaluate('(()=>{let i=document.querySelector(".result-photo"),r=i.getBoundingClientRect();return r.width<=i.naturalWidth && r.height<=i.naturalHeight && document.documentElement.scrollWidth<=innerWidth+1})()')
                        c.screenshot(out/(label+'-mobile.png'));screens.append(label+'-mobile.png')

        finally:
            browser.terminate();browser.wait(timeout=15)
    with sqlite3.connect(database) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute('SELECT COUNT(*) FROM inspections').fetchone()[0]==len(cases)+2
        assert db.execute('SELECT COUNT(*) FROM check_results').fetchone()[0]==12*(len(cases)+2)
    original=Path('data/inspections.sqlite3').resolve();db=sqlite3.connect(original.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        legacy=db.execute('SELECT * FROM inspections LIMIT 1').fetchone()
        assert legacy is not None and 'Photo result' in render_result(dict(legacy))
    finally:db.close()
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in hashes.items())
    target.write_text(json.dumps({'status':'passed','inspections':len(cases)+2,'check_records':12*(len(cases)+2),'process_restart':True,'duplicate_submission':True,
        'invalid_input_rejected':True,'keyboard_and_native_validation':True,'desktop_and_mobile_browser':True,'fractional_calibrated_submission':True,
        'browser':version,'focus_sequence':focus,'screenshots':screens,'existing_legacy_result_rendered':True,
        'protected_user_database_and_benchmark_unchanged':True,'saved':saved},indent=2),encoding='utf-8')
    print('PASS: real Chrome desktop/mobile, keyboard/native validation, fractional profile submit, screenshots, saved inspections across all contexts, restart and legacy results.')


if __name__=='__main__':main()
