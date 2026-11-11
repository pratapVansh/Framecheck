import json
import re
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from inspector import auto_inspect,modern_ui
from inspector.auto_inspect import detect,inspect_auto
from inspector.images import png_bytes
from inspector.regions import analysis_image


def stub_segmentation(monkeypatch, rgb, foreground, labels):
    shape=analysis_image(rgb).shape[1::-1]
    mask=cv2.resize(foreground.astype(np.uint8),shape,interpolation=cv2.INTER_NEAREST).astype(bool)
    monkeypatch.setattr(auto_inspect,'segment',lambda image:SimpleNamespace(
        mask=mask,reliable=True,reasons=[],diagnostics={}))
    monkeypatch.setattr(auto_inspect,'_model',lambda:(lambda image,**kwargs:labels,{'model_id':'test-only-fixture'}))


def person_scene():
    rgb=np.full((900,1200,3),245,np.uint8)
    labels=np.zeros(rgb.shape[:2],np.uint8)
    labels[50:170,260:390]=11
    labels[170:440,210:440]=4
    labels[440:800,240:415]=6
    labels[250:600,160:210]=15
    labels[250:600,440:490]=14
    for identity in [4,6]:
        y,x=np.indices(rgb.shape[:2]);texture=(50+x%90+30*((y//8)%2)).astype(np.uint8)
        rgb[labels==identity]=np.repeat(texture[:,:,None],3,axis=2)[labels==identity]
    rgb[np.isin(labels,[11,14,15])]=[180,140,110]
    return rgb,labels


def test_all_apparel_and_separated_generic_item_are_kept(monkeypatch):
    rgb,labels=person_scene()
    foreground=labels>0;foreground[320:540,800:1050]=True
    rgb[320:540,800:1050]=[60,80,90]
    stub_segmentation(monkeypatch,rgb,foreground,labels)
    masks,coverage=detect(rgb)
    assert len(masks)==3
    assert any(mask[550,300] for mask in masks),'Trousers must not be discarded'
    assert any(mask[400,900] for mask in masks),'A separate non-clothing product must not be discarded'
    assert coverage['coverage_complete'] is True
    assert not any(mask[100,300] or mask[400,180] for mask in masks),'Face/arms must stay outside products'


def test_unexplained_item_preserves_detected_apparel_and_requests_only_coverage(monkeypatch):
    rgb,labels=person_scene()
    foreground=labels>0;foreground[350:550,480:650]=True
    stub_segmentation(monkeypatch,rgb,foreground,labels)
    masks,coverage=detect(rgb)
    assert len(masks)==2
    assert coverage['coverage_complete'] is False
    assert 'unexplained' in coverage['reason'].lower()


def test_fragmented_unknown_foreground_keeps_coverage_metadata_json_safe(monkeypatch):
    rgb,labels=person_scene();foreground=labels>0
    for y in range(80,321,40):
        for x in range(800,1041,40):foreground[y:y+20,x:x+20]=True
    stub_segmentation(monkeypatch,rgb,foreground,labels)
    masks,coverage=detect(rgb)
    assert len(masks)==2 and not coverage['coverage_complete']
    json.dumps(coverage)


@pytest.mark.parametrize('class_id',[16,17])
def test_bag_or_scarf_is_not_a_body_part(monkeypatch,class_id):
    rgb=np.full((700,700,3),245,np.uint8)
    labels=np.zeros(rgb.shape[:2],np.uint8);labels[80:620,250:420]=class_id
    rgb[labels==class_id]=80
    stub_segmentation(monkeypatch,rgb,labels>0,labels)
    masks,coverage=detect(rgb)
    assert len(masks)==1 and coverage['method']=='general_foreground'
    assert coverage['coverage_complete'] is True


def test_leg_labels_support_person_and_do_not_become_products(monkeypatch):
    rgb,labels=person_scene()
    labels[np.isin(labels,[11,14,15])]=0
    labels[800:880,240:305]=12;labels[800:880,350:415]=13
    stub_segmentation(monkeypatch,rgb,labels>0,labels)
    masks,coverage=detect(rgb)
    assert len(masks)==2
    assert not any(mask[850,260] or mask[850,370] for mask in masks)


def test_clothing_parser_labels_on_a_separate_object_do_not_override_general_locator(monkeypatch):
    rgb,labels=person_scene()
    labels[320:540,800:1050]=4
    foreground=labels>0
    stub_segmentation(monkeypatch,rgb,foreground,labels)
    masks,coverage=detect(rgb)
    assert len(masks)==3
    descriptions=coverage['region_descriptions']
    assert sum(d.get('class_id')==4 for d in descriptions)==1
    assert any(d['source']=='general foreground' for d in descriptions)


def test_model_failure_keeps_clear_items_without_treating_the_person_as_a_product(monkeypatch):
    rgb,labels=person_scene()
    foreground=labels>0;foreground[320:540,800:1050]=True
    stub_segmentation(monkeypatch,rgb,foreground,labels)
    def missing_model():raise RuntimeError('Unavailable test model')
    monkeypatch.setattr(auto_inspect,'_model',missing_model)
    masks,coverage=detect(rgb)
    assert len(masks)==1 and masks[0][400,900]
    assert not masks[0][250,300]
    assert coverage['coverage_complete'] is False


@pytest.mark.parametrize('class_id',[7,16,17])
def test_person_associated_products_do_not_require_an_upper_clothes_label(monkeypatch,class_id):
    rgb,labels=person_scene();labels[labels==4]=class_id
    stub_segmentation(monkeypatch,rgb,labels>0,labels)
    masks,coverage=detect(rgb)
    assert any(d.get('class_id')==class_id for d in coverage['region_descriptions'])
    assert not any(mask[100,300] for mask in masks)


def test_coverage_review_is_listed_with_other_available_findings(monkeypatch):
    rgb,labels=person_scene()
    mask=labels==4
    monkeypatch.setattr(auto_inspect,'detect',lambda image, **kwargs:([mask],{
        'method':'test','coverage_complete':False,'reason':'An unexplained object remains.','disclosure':'Test'}))
    result=inspect_auto(png_bytes(rgb))
    record={'result_json':result.to_json(),'filename':'test-only-scene.png'}
    html=modern_ui.render_result(record)
    assert 'data-finding-id="photo.coverage" data-statement' in html
    assert 'data-finding-id="photo.resolution" data-statement' in html
    assert set(re.findall(r'data-finding-id="([^"]+)" data-statement',html))=={c.identifier for c in result.checks}


def test_product_failure_wins_without_changing_other_items(monkeypatch):
    rgb,labels=person_scene()
    masks=[labels==4,labels==6]
    monkeypatch.setattr(auto_inspect,'detect',lambda image, **kwargs:(masks,{
        'method':'test','coverage_complete':False,'reason':'Unknown target.','disclosure':'Test'}))
    original=auto_inspect.product_findings
    def local(image,mask,number,policy):
        checks,item=original(image,mask,number,policy)
        if number==2:
            check=next(c for c in checks if c.identifier=='product-2.noise')
            check.status='Fail';check.explanation='Confirmed critical test-only failure.'
        return checks,item
    monkeypatch.setattr(auto_inspect,'product_findings',local)
    result=inspect_auto(png_bytes(rgb))
    assert result.status=='Needs Improvement'
    assert next(c for c in result.checks if c.identifier=='photo.coverage').status=='Manual Review'
    assert next(c for c in result.checks if c.identifier=='product-1.detail').status=='Pass'
