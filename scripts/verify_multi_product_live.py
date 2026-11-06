"""Verify a normal-terminal server on 8505 without requesting any Groq explanations."""
import hashlib
import http.client
import json
import re
from pathlib import Path
import sqlite3
import urllib.request
from uuid import uuid4

from inspector.models import CheckResult,aggregate
from inspector.narration import approved_wording,validate

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/multi-product-debug/live-8505'
BASE='http://127.0.0.1:8505'


def read(path):
    with urllib.request.urlopen(BASE+path,timeout=10) as response:return response.read()


def run():
    # Reading HTML with urllib does not execute its optional Groq-fetch JavaScript.
    old_id='d041db85-0eb8-4f7a-98a5-dcbb6135c94d'
    html=read('/inspection/'+old_id).decode()
    assert 'data-finding-id="photo.coverage" data-statement' in html and 'Combined mask of all numbered regions' in html,'Restart Python on 8505 to load the updated code first.'
    OUT.mkdir(parents=True,exist_ok=True)
    cases=[('single-shoe','474ac439-9de4-4ec5-b58d-301d6a753d1b',1,'Pass'),
           ('confirmed-two-people',old_id,4,'Manual Review'),
           ('single-person-warm','e6d143f8-5bd8-4a30-86e7-8cd9fc7d5615',2,'Manual Review')]
    results=[]
    with sqlite3.connect(ROOT/'data/inspections.sqlite3') as db:
        for name,source_id,count,expected in cases:
            folder=OUT/name;folder.mkdir(exist_ok=True)
            source,old=db.execute('select original_png,result_json from inspections where id=?',(source_id,)).fetchone()
            if (folder/'report.json').exists():
                result=json.loads((folder/'report.json').read_text());identity=result['inspection_id']
            else:
                identity=str(uuid4());boundary='framecheck-'+uuid4().hex
                parts=[]
                for key,value in [('submission_id',identity),('one_click','yes')]:
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}-recheck.png"\r\nContent-Type: image/png\r\n\r\n'.encode()+source+b'\r\n')
                body=b''.join(parts)+f'--{boundary}--\r\n'.encode()
                connection=http.client.HTTPConnection('127.0.0.1',8505,timeout=60)
                connection.request('POST','/inspect',body,{'Content-Type':'multipart/form-data; boundary='+boundary})
                response=connection.getresponse();response.read();assert response.status==303,response.status
                connection.close()
                result=json.loads(read('/report/'+identity+'.json'))
                (folder/'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            assert result['pipeline_version']=='1.1.0-multi-product-dip'
            assert len(result['segmentation']['products'])==count
            assert result['status']==expected==str(aggregate([CheckResult(**c) for c in result['checks']]))
            assert result['segmentation']['coverage']['coverage_complete'] is True
            assert result['input_hash']==hashlib.sha256(source).hexdigest()
            assert result['segmentation']['whole_photo']==json.loads(old)['segmentation']['whole_photo']
            validate({'items':approved_wording(result)},approved_wording(result))
            rendered=read('/inspection/'+identity).decode()
            assert rendered.count('class="product-head"')==count
            assert 'id="coverage-question"' not in rendered
            assert 'data-finding-id="photo.coverage" data-statement' in rendered
            assert set(re.findall(r'data-finding-id="([^"]+)" data-statement',rendered))=={c['identifier'] for c in result['checks']}
            for kind in ['original','mask','overlay']:(folder/(kind+'.png')).write_bytes(read(f'/evidence/{identity}/{kind}.png'))
            results.append({'source_id':source_id,'id':identity,'url':BASE+'/inspection/'+identity,
                'products':count,'coverage_complete':True,'verdict':expected,'duration_ms':result['duration_ms'],
                'reviews':[(c['identifier'],c.get('reason_code')) for c in result['checks'] if c['status']=='Manual Review'],
                'whole_photo_unchanged':True,'new_version_served':True,'grounding_contract_valid':True,'all_findings_rendered':True})
    before=json.loads((ROOT/'reports/multi-product-debug/before.json').read_text())
    with sqlite3.connect(ROOT/'data/inspections.sqlite3') as db:
        reports=dict(db.execute('select id,result_json from inspections'))
    for identity,digest in before['reports'].items():assert hashlib.sha256(reports[identity].encode()).hexdigest()==digest,identity
    assert hashlib.sha256((ROOT/'data/one-click-policy.json').read_bytes()).hexdigest()==before['policy_sha256']
    output={'cases':results,'original_reports_preserved':len(before['reports']),
        'total_inspections':len(reports),'thresholds_unchanged':True,'generation_requests':0,
        'note':'Actual updated normal-terminal server verified; no /explanation requests or browser JS executed.'}
    (OUT/'verification.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print(json.dumps(output,indent=2))


if __name__=='__main__':run()
