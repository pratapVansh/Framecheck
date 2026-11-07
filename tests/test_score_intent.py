"""Focused score/intent contracts; procedural masks are not accuracy validation."""
import copy
import json
import sqlite3

import numpy as np
import pytest

from inspector import auto_inspect, modern_ui, narration
from inspector.auto_inspect import inspect_auto, amend_inspection
from inspector.images import png_bytes
from inspector.scoring import WEIGHTS, score_photo, score_statement
from inspector.storage import load_inspection, save_inspection
from test_narration import response_for
from test_one_click import normal_image, unit_policy, record
from test_web import server, request, multipart


def perfect_checks(numbers=(1,)):
    return [{'identifier': key if key.startswith('photo.') else f'product-{n}.{key.split(".")[1]}',
             'status': 'Pass', 'explanation': 'Test-only supported finding.', 'required': True}
            for key in WEIGHTS for n in (numbers if key.startswith('product.') else [0])]


def test_fixed_budget_and_missing_disabled_or_review_never_earn_credit():
    checks=perfect_checks();products=[{'number':1}]
    assert sum(WEIGHTS.values())==100
    score=score_photo(checks,products,'Pass')
    assert score['score']==score['assessed_coverage_percent']==100
    for state in ['Manual Review','Disabled',None]:
        changed=copy.deepcopy(checks)
        if state is None:changed=[c for c in changed if c['identifier']!='photo.blur']
        else:next(c for c in changed if c['identifier']=='photo.blur')['status']=state
        score=score_photo(changed,products,'Pass')
        assert score['score']==score['assessed_coverage_percent']==86
        assert score['uncertainty_withheld']==14 and score['possible_range']==[86,100]
        assert score['verdict']=='Manual Review'
    assert score_photo([],[],'Pass')['score']==0
    missing=score_photo(perfect_checks(),[],'Manual Review')
    assert missing['uncertainty_withheld']==30


def test_worst_intended_product_and_independent_photo_failures():
    checks=perfect_checks((1,2))
    next(c for c in checks if c['identifier']=='product-2.blur')['status']='Fail'
    next(c for c in checks if c['identifier']=='photo.noise')['status']='Fail'
    products=[{'number':1},{'number':2}]
    score=score_photo(checks,products,'Fail')
    assert score['score']==82 and score['confirmed_deductions']==18
    assert score['assessed_coverage_percent']==100 and score['possible_range']==[82,82]
    products[1]['intended']=False
    score=score_photo(checks,products,'Fail')
    assert score['score']==90 and score['confirmed_deductions']==10 and score['verdict']=='Fail'
    products.extend({'number':n} for n in range(3,6))
    # Missing additional regions cannot improve any component.
    assert score_photo(checks,products,'Fail')['score']<=90


def scene(monkeypatch, rects, labels=None):
    rgb=np.full((1000,1200,3),245,np.uint8);masks=[]
    yy,xx=np.indices(rgb.shape[:2]);texture=(40+xx%100+40*((yy//2)%2)).astype(np.uint8)
    for x,y,w,h in rects:
        mask=np.zeros(rgb.shape[:2],bool);mask[y:y+h,x:x+w]=True;masks.append(mask)
        rgb[mask]=np.repeat(texture[:,:,None],3,axis=2)[mask]
    monkeypatch.setattr(auto_inspect,'detect',lambda image:(masks,{
        'method':'test-fixture','coverage_complete':True,'reason':'Procedural regions.',
        'disclosure':'Not a real detector accuracy test.',
        'region_descriptions':[{'label':label} for label in labels or ['Generic']*len(masks)]}))
    return inspect_auto(png_bytes(rgb))


@pytest.mark.parametrize('labels',[['Top','Trousers','Top','Trousers'],['Bag','Box','Shoe','Bottle']])
def test_two_complete_products_keep_edge_fragments_as_context_and_allow_override(monkeypatch,labels):
    parent=scene(monkeypatch,[(140,230,350,520),(180,830,260,170),
                              (700,270,320,480),(730,830,240,170)],labels)
    products=parent.segmentation['products']
    assert [p['number'] for p in products if p['intended']]==[1,2]
    assert all(not c.required for c in parent.checks if c.identifier.startswith(('product-2.','product-4.')))
    assert all(c.reason_code=='possible_cropping' for c in parent.checks if c.identifier in {'product-2.edge','product-4.edge'})
    assert next(c for c in parent.to_dict()['quality_score']['components'] if c['component']=='product.edge')['status']=='Pass'
    saved=record(parent);before=saved['result_json']
    child=amend_inspection(saved,intended_numbers=[1,2,3,4],confirm_coverage=True)
    assert saved['result_json']==before
    assert all(p['intended'] and p['intent_source']=='user' for p in child.segmentation['products'])
    assert next(c for c in child.checks if c.identifier=='product-2.edge').required
    edge=next(c for c in child.to_dict()['quality_score']['components'] if c['component']=='product.edge')
    assert edge['withheld']==5
    rendered=modern_ui.render_result({**saved,'filename':'fixture.png'})
    assert '2 intended products' in rendered and 'Incidental context' in rendered
    assert 'Whole-photo quality' in rendered and 'Assessed coverage' in rendered
    assert 'data-product-number="2"' in rendered and 'Override intended products' in rendered
    assert [p['region_id'] for p in products]==[1,3]
    assert [p['region_id'] for p in parent.segmentation['incidental_regions']]==[2,4]
    assert all(p['number'] is None for p in parent.segmentation['incidental_regions'])


def test_full_length_and_dominant_cropped_products_remain_intended(monkeypatch):
    complete=scene(monkeypatch,[(400,65,380,870)])
    assert complete.segmentation['products'][0]['intended']
    assert next(c for c in complete.checks if c.identifier=='product-1.edge').status=='Pass'
    cropped=scene(monkeypatch,[(290,0,620,820),(30,650,200,220)])
    assert all(p['intended'] for p in cropped.segmentation['products'])
    edge=next(c for c in cropped.checks if c.identifier=='product-1.edge')
    assert edge.required and edge.reason_code=='possible_cropping'
    assert cropped.segmentation['products'][0]['intent_ambiguous']
    assert next(c for c in cropped.to_dict()['quality_score']['components'] if c['component']=='product.edge')['withheld']==5


def test_score_roundtrips_storage_and_old_reports_render_without_rewriting(tmp_path,normal_image,unit_policy):
    result=inspect_auto(png_bytes(normal_image),calibration=unit_policy)
    database=tmp_path/'score.sqlite3'
    save_inspection(result,'fixture.png',database)
    saved=load_inspection(result.inspection_id,database)
    assert json.loads(saved['result_json'])==result.to_dict()
    assert saved['result_json']==result.to_json()
    old=json.loads(saved['result_json']);old.pop('quality_score')
    old['pipeline_version']='1.2.0-photo-scope-dip'
    old_json=json.dumps(old)
    assert 'Not scored' in modern_ui.render_result({**saved,'result_json':old_json})
    assert json.loads(old_json)==old


@pytest.mark.parametrize('bad',[False,True])
def test_groq_receives_score_evidence_and_cannot_change_score(monkeypatch,tmp_path,normal_image,unit_policy,bad):
    report=inspect_auto(png_bytes(normal_image),calibration=unit_policy).to_dict()
    before=copy.deepcopy(report)
    monkeypatch.setattr(narration,'ENV_FILE',tmp_path/'absent')
    monkeypatch.setenv('GROQ_API_KEY','fixture-key')
    def provider(request,timeout):
        data=json.loads(json.loads(request.data)['messages'][1]['content'])
        assert data['score_evidence']==report['quality_score']
        text=data['approved_score_explanation']
        return response_for({'items':data['approved_items'],'score_explanation':'Score: 100/100, verified.' if bad else text})
    monkeypatch.setattr(narration.urllib.request,'urlopen',provider)
    narration._explain.cache_clear()
    try:
        output=narration.explain(report)
        assert output['source']==('local' if bad else 'groq_validated')
        assert output['score_explanation']==score_statement(report['quality_score'])
        assert report==before
    finally:narration._explain.cache_clear()


def test_http_report_includes_score_and_override_creates_immutable_child(server,monkeypatch,normal_image,unit_policy):
    monkeypatch.setattr(auto_inspect,'policy',lambda:unit_policy)
    from uuid import uuid4
    identity=str(uuid4())
    body,kind=multipart({'one_click':'yes','submission_id':identity},png_bytes(normal_image))
    assert request(server,'POST','/inspect',body,{'Content-Type':kind})[0]==303
    raw=request(server,'GET',f'/report/{identity}.json')[2]
    parent=json.loads(raw)
    assert parent['quality_score']['uncertainty_withheld']>=10
    child_id=str(uuid4())
    body,kind=multipart({'source_inspection_id':identity,'submission_id':child_id,
                         'intent_selection':'yes','intended_1':'yes','coverage_confirm':'yes'})
    assert request(server,'POST','/confirm-coverage',body,{'Content-Type':kind})[0]==303
    child=json.loads(request(server,'GET',f'/report/{child_id}.json')[2])
    assert child['quality_score']['score']==100
    assert child['quality_score']['assessed_coverage_percent']==100
    assert request(server,'GET',f'/report/{identity}.json')[2]==raw


def test_new_partition_numbers_only_intended_products_and_scores_stable_region_ids(monkeypatch):
    parent=scene(monkeypatch,[(140,230,350,520),(180,830,260,168),
                              (700,270,320,480),(730,830,240,168)])
    meta=parent.segmentation
    assert meta['product_count']==2 and meta['incidental_count']==2 and meta['detection_count']==4
    assert [p['number'] for p in meta['products']]==[1,2]
    assert [p['region_id'] for p in meta['products']]==[1,3]
    assert all(p['number'] is None for p in meta['incidental_regions'])
    score=parent.to_dict()['quality_score']
    blur=next(c for c in score['components'] if c['component']=='product.blur')
    assert blur['finding_ids']==['product-1.blur','product-3.blur']
    html=modern_ui.render_result({**record(parent),'filename':'fixture.png'})
    photo=modern_ui.numbered_photo(parent.to_dict(),parent.inspection_id)
    assert photo.count('data-product-number=')==2
    assert html.count('data-intended-product=')==2
    assert 'data-product-number="3"' not in photo
    # All detailed statements, including exclusions, stay inside the outer Details.
    before,details=html.split('<details class="review-details"',1)
    assert 'data-statement' not in before
    assert 'id="incidental-context"' in details
    assert all(f'data-finding-id="{c.identifier}" data-statement' in details for c in parent.checks)
    restored=amend_inspection(record(parent),intended_numbers=[3],confirm_coverage=True)
    assert restored.segmentation['products'][0]['number']==1
    assert restored.segmentation['products'][0]['region_id']==3
    assert next(c for c in restored.to_dict()['quality_score']['components'] if c['component']=='product.blur')['finding_ids']==['product-3.blur']
    marked=amend_inspection(record(restored),points=[[20,30],[90,30],[90,180],[20,180]])
    assert [p['region_id'] for p in auto_inspect.detected_regions(marked.segmentation)]==[1,2,3,4,5]


def test_reinspection_keeps_historical_json_and_applies_current_partition(server,monkeypatch):
    from uuid import uuid4
    parent=scene(monkeypatch,[(140,230,350,520),(180,830,260,170),
                              (700,270,320,480),(730,830,240,170)])
    saved=parent.to_dict()
    old=copy.deepcopy(saved)
    old['segmentation']['products']=auto_inspect.detected_regions(old['segmentation'])
    old['segmentation'].pop('incidental_regions')
    old['segmentation'].pop('report_schema_version')
    for region in old['segmentation']['products']:
        region.update(number=region['region_id'],intended=True)
    old['pipeline_version']='1.2.0-photo-scope-dip'
    save_inspection(parent,'historical.png',server.database)
    raw=json.dumps(old)
    with sqlite3.connect(server.database) as db:
        db.execute('update inspections set result_json=? where id=?',(raw,parent.inspection_id))
    child_id=str(uuid4())
    body,kind=multipart({'source_inspection_id':parent.inspection_id,'submission_id':child_id})
    assert request(server,'POST','/reinspect',body,{'Content-Type':kind})[0]==303
    child=json.loads(request(server,'GET',f'/report/{child_id}.json')[2])
    assert child['segmentation']['product_count']==2
    assert child['segmentation']['source_inspection_id']==parent.inspection_id
    assert request(server,'GET',f'/report/{parent.inspection_id}.json')[2].decode()==raw
