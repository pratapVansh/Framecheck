import json
from uuid import uuid4

import numpy as np
import pytest

from inspector import auto_inspect
from inspector.auto_inspect import inspect_auto,amend_inspection,judge
from inspector.images import png_bytes
from inspector.storage import load_inspection
from inspector.web import make_server
from test_web import request,multipart,server


@pytest.fixture
def normal_image():
    rgb=np.full((640,900,3),245,np.uint8)
    y,x=np.indices((220,300))
    texture=(65+(x%100)+25*((y//12)%2)).astype(np.uint8)
    rgb[210:430,280:580]=np.repeat(texture[:,:,None],3,axis=2)
    return rgb


@pytest.fixture
def unit_policy():
    low=lambda:{'direction':'low','pass':.001,'fail':.00001}
    return {'name':'Synthetic test policy, not calibration evidence','feature_version':'normal-dip-v1','metrics':{
        'photo.blur':low(),'product.blur':low(),
        'photo.contrast':{'direction':'low','pass':40.,'fail':10.},
        'photo.noise':{'direction':'high','pass':8.,'fail':20.},
        'product.noise':{'direction':'high','pass':8.,'fail':20.},
        'photo.shadow_loss':{'direction':'high','pass':0.,'fail':3.},
        'photo.highlight_loss':{'direction':'high','pass':0.,'fail':3.}},
        'composition':{**auto_inspect.GROUP_COMPOSITION,'min_coverage':.2}}


def record(result):
    return {'result_json':result.to_json(),'original_png':result.original_png,'mask_png':result.mask_png,'overlay_png':result.overlay_png}


def test_clear_can_pass_and_missing_coverage_blocks_only_required_check(monkeypatch,normal_image,unit_policy):
    report=inspect_auto(png_bytes(normal_image),calibration=unit_policy)
    assert len(report.segmentation['products'])==1 and report.status=='Manual Review'
    confirmed=amend_inspection(record(report),confirm_coverage=True)
    assert confirmed.status=='Pass'
    assert all(c.status=='Pass' for c in confirmed.checks if c.required)
    region=np.zeros(normal_image.shape[:2],bool);region[210:430,280:580]=True
    monkeypatch.setattr(auto_inspect,'detect',lambda rgb:([region],{'method':'test','coverage_complete':False,'reason':'A second item may be missing.','disclosure':'Test only'}))
    uncertain=inspect_auto(png_bytes(normal_image),calibration=unit_policy)
    assert uncertain.status=='Manual Review'
    assert next(c for c in uncertain.checks if c.identifier=='photo.coverage').status=='Manual Review'
    assert all(c.status=='Pass' for c in uncertain.checks if c.identifier.startswith('product-'))
    assert amend_inspection(record(uncertain),confirm_coverage=True).status=='Pass'
    assert uncertain.segmentation['whole_photo']==report.segmentation['whole_photo']


def test_multiple_products_automatically_receive_all_local_checks(normal_image,unit_policy):
    rgb=np.full_like(normal_image,245)
    rgb[:, :450]=normal_image[:, :450]
    rgb[:,450:]=normal_image[:,:450]
    # Keep two separated complete textured regions.
    rgb=np.concatenate([normal_image,normal_image],axis=1)
    result=inspect_auto(png_bytes(rgb),calibration=unit_policy)
    assert len(result.segmentation['products'])==2
    assert {c.identifier for c in result.checks if c.identifier.startswith('product-2.')}=={
        'product-2.detail','product-2.blur','product-2.noise','product-2.edge',
        'product-2.contrast','product-2.clipping','product-2.mask_completeness'}
    assert result.status=='Manual Review'
    assert amend_inspection(record(result),confirm_coverage=True).status=='Pass'


def test_confirmed_signal_loss_fails_even_when_detection_missing(normal_image,unit_policy):
    damaged=(normal_image.astype(float)*.01).astype(np.uint8)
    report=inspect_auto(png_bytes(damaged),calibration=unit_policy)
    by_id={c.identifier:c for c in report.checks}
    assert report.status=='Fail'
    assert by_id['photo.resolution'].status=='Pass'
    assert by_id['photo.shadow_loss'].status=='Fail'
    assert by_id['photo.coverage'].status=='Manual Review'


def test_missing_noise_affects_only_noise_and_noise_dependent_sharpness(monkeypatch,normal_image,unit_policy):
    original=auto_inspect.signal
    def missing(rgb,mask=None):
        q=original(rgb,mask);q['noise']=None;return q
    monkeypatch.setattr(auto_inspect,'signal',missing)
    report=inspect_auto(png_bytes(normal_image),calibration=unit_policy)
    checks={c.identifier:c for c in report.checks}
    assert checks['photo.noise'].status=='Manual Review'
    assert checks['photo.blur'].status=='Manual Review'
    assert checks['photo.contrast'].status==checks['photo.resolution'].status=='Pass'
    assert checks['photo.coverage'].status=='Manual Review'
    child=amend_inspection(record(report),confirm_coverage=True)
    assert next(c for c in child.checks if c.identifier=='photo.coverage').status=='Pass'
    assert child.status=='Manual Review'


def test_unvalidated_fail_range_does_not_disable_pass_or_other_checks():
    rule={'direction':'low','pass':10,'fail':5,'fail_validated':False}
    assert judge('photo.blur',2,rule).reason_code=='fail_region_unvalidated'
    assert judge('photo.blur',20,rule).status=='Pass'
    assert judge('photo.contrast',2,{**rule,'fail_validated':True}).status=='Fail'


def test_confirmation_and_marks_preserve_parent_and_available_findings(monkeypatch,normal_image,unit_policy):
    region=np.zeros(normal_image.shape[:2],bool);region[210:430,280:580]=True
    monkeypatch.setattr(auto_inspect,'detect',lambda rgb:([region],{'method':'test','coverage_complete':False,'reason':'Check coverage','disclosure':'Test'}))
    parent=inspect_auto(png_bytes(normal_image),calibration=unit_policy)
    source=record(parent);saved=source['result_json']
    child=amend_inspection(source,confirm_coverage=True)
    assert child.status=='Pass' and child.inspection_id!=parent.inspection_id
    assert source['result_json']==saved
    assert child.segmentation['whole_photo']==parent.segmentation['whole_photo']
    marked=amend_inspection(source,points=[[25,25],[130,25],[130,150],[25,150]])
    assert len(marked.segmentation['products'])==2
    assert [c.to_dict() for c in marked.checks[:6]]==[c.to_dict() for c in parent.checks[:6]]
    assert marked.checks[-1].status=='Manual Review'


def test_live_http_contract_local_results_and_background_explanation(server,monkeypatch,normal_image,unit_policy,tmp_path):
    monkeypatch.setattr('inspector.narration.ENV_FILE',tmp_path/'.env')
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    monkeypatch.setattr(auto_inspect,'policy',lambda:unit_policy)
    code,_,body=request(server,'GET','/')
    assert code==200 and b'name="one_click"' in body
    assert b'name="photo_context"' not in body and b'name="product_category"' not in body
    identity=str(uuid4())
    form,kind=multipart({'submission_id':identity,'one_click':'yes'},png_bytes(normal_image))
    assert request(server,'POST','/inspect',form,{'Content-Type':kind})[0]==303
    before=load_inspection(identity,server.database)['result_json']
    code,_,body=request(server,'GET',f'/inspection/{identity}')
    assert code==200 and b'What we found' in body and b'How to fix it' in body
    assert b'scores, thresholds' in body and b'photo.coverage' in body
    code,_,payload=request(server,'GET',f'/explanation/{identity}.json')
    narration=json.loads(payload)
    assert code==200 and narration['source']=='local'
    assert all(item['finding_id'] for item in narration['items'])
    assert load_inspection(identity,server.database)['result_json']==before


def test_http_intended_selection_excludes_without_erasing_findings(server,monkeypatch,normal_image,unit_policy):
    monkeypatch.setattr(auto_inspect,'policy',lambda:unit_policy)
    identity=str(uuid4())
    image=np.concatenate([normal_image,normal_image],axis=1)
    form,kind=multipart({'submission_id':identity,'one_click':'yes'},png_bytes(image))
    assert request(server,'POST','/inspect',form,{'Content-Type':kind})[0]==303
    before=load_inspection(identity,server.database)['result_json']
    child_id=str(uuid4())
    fields={'submission_id':child_id,'source_inspection_id':identity,'intent_selection':'yes',
            'intended_1':'yes','coverage_confirm':'yes'}
    form,kind=multipart(fields)
    assert request(server,'POST','/confirm-coverage',form,{'Content-Type':kind})[0]==303
    child=json.loads(load_inspection(child_id,server.database)['result_json'])
    assert child['segmentation']['products'][0]['intended']
    assert not child['segmentation']['products'][1]['intended']
    assert all(not c['required'] for c in child['checks'] if c['identifier'].startswith('product-2.'))
    assert len(child['checks'])==len(json.loads(before)['checks'])
    assert load_inspection(identity,server.database)['result_json']==before
    fields.pop('intended_1');fields['submission_id']=str(uuid4())
    form,kind=multipart(fields)
    code,_,body=request(server,'POST','/confirm-coverage',form,{'Content-Type':kind})
    assert code==400 and b'What we found' in body
    assert load_inspection(fields['submission_id'],server.database) is None
    form,kind=multipart({'submission_id':str(uuid4()),'source_inspection_id':identity,'polygon':'[]'})
    code,_,body=request(server,'POST','/mark-product',form,{'Content-Type':kind})
    assert code==400 and b'role="alert"' in body and b'What we found' in body
    assert load_inspection(identity,server.database)['result_json']==before
