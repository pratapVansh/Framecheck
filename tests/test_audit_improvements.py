"""Evidence-backed integration controls; these fixtures are not calibration data."""
import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from inspector import auto_inspect as auto, modern_ui, narration
from inspector.images import decode_image, png_bytes
from inspector.storage import load_inspection
from test_one_click import normal_image, unit_policy
from test_photo_scope import audit_image, checks, record
from test_web import server, request, multipart
from test_multi_product import person_scene, stub_segmentation


@pytest.fixture(autouse=True)
def clear_localization_cache():
    auto.LOCALIZATION_CACHE.clear()
    yield
    auto.LOCALIZATION_CACHE.clear()


def test_raw_gap_proposes_separate_products_without_claiming_a_verified_count():
    mask=np.zeros((200,400),bool);mask[40:160,60:340]=True
    raw=mask.copy();raw[:,199:201]=False
    regions,evidence=auto.separate_foreground([mask],SimpleNamespace(mask=mask,raw_mask=raw))
    assert len(regions)==2 and not (regions[0]&regions[1]).any()
    assert evidence[0]['reason']=='raw_gap'
    assert evidence[0]['proposed_parts']==2 and not evidence[0]['item_count_verified']
    assert sum(m.sum() for m in regions)==raw.sum()


def test_touching_control_requires_count_review_but_separated_control_passes():
    touching=auto.inspect_auto(audit_image('merged'))
    assert touching.status=='Needs Review'
    assert checks(touching)['photo.coverage'].reason_code=='item_count_unresolved'
    assert touching.segmentation['coverage']['separation_evidence']
    separated=auto.inspect_auto(audit_image('separated'))
    assert separated.segmentation['product_count']==2 and separated.status=='Pass'
    rgb,_=decode_image(audit_image('separated'))
    # A short connection is ambiguous even when the image has two substantial lobes.
    rgb[305:307,410:490]=80
    joined=auto.inspect_auto(png_bytes(rgb))
    assert joined.status=='Needs Review'
    assert checks(joined)['photo.coverage'].reason_code=='item_count_unresolved'


def test_merged_semantic_clothing_region_also_retains_count_uncertainty(monkeypatch):
    rgb,labels=person_scene()
    rgb=np.pad(rgb,((0,700),(0,0),(0,0)),constant_values=245)
    labels=np.pad(labels,((0,700),(0,0)))
    labels[800:1450,240:415]=12
    rgb[labels==12]=[180,140,110]
    labels[170:440,210:900]=4
    rgb[labels==4]=70
    stub_segmentation(monkeypatch,rgb,labels>0,labels)
    masks,coverage=auto.detect(rgb)
    assert masks and coverage['coverage_complete']
    assert coverage['item_count_uncertain']
    assert any(e.get('class_id')==4 for e in coverage['separation_evidence'])


def test_severe_framing_blocks_a_high_score_and_does_not_create_pending_deductions():
    result=auto.inspect_auto(audit_image('corner'))
    score=result.score()
    assert score['score']==90 and score['confirmed_deductions']==10
    assert score['uncertainty_withheld']==0 and result.status=='Needs Improvement'
    assert set(score['critical_failures'])=={'photo.group_size','photo.group_placement','photo.group_margins'}
    assert all(checks(result)[identity].measurement['severe_framing'] for identity in score['critical_failures'])


@pytest.mark.parametrize('bbox,advice',[([160,200,250,220],'Move closer'),([10,10,880,620],'Step back')])
def test_framing_advice_uses_measured_direction(bbox,advice):
    group=auto.group_findings([{'bbox':bbox}],900,640,auto.policy(),accepted=True)
    item=next(c for c in group if c.identifier=='photo.group_size')
    assert narration.approved_wording({'checks':[item.to_dict()]})[0]['fix'].startswith(advice)


def test_sparse_background_uses_supported_subject_focus_and_retains_raw_observation():
    raw=audit_image('small');rgb,_=decode_image(raw)
    original,_=auto.photo_findings(rgb,auto.policy())
    original_focus=next(c for c in original if c.identifier=='photo.blur')
    result=auto.inspect_auto(raw);focus=checks(result)['photo.blur']
    assert original_focus.status=='Manual Review' and focus.status=='Pass'
    assert focus.measurement['score']==original_focus.measurement['score']
    assert focus.measurement['assessment_scope']=='included_products'
    assert focus.measurement['whole_photo_observation']['status']=='Manual Review'
    assert focus.measurement['evidence_finding_ids']==['product-1.blur']
    # Uncertain contrast remains pending; severe small framing still blocks Pass.
    assert checks(result)['photo.contrast'].status=='Manual Review'
    assert result.score()['uncertainty_withheld']==8 and result.status=='Needs Improvement'


def test_uncertain_product_focus_or_changed_scope_cannot_clear_photo_uncertainty(monkeypatch):
    original=auto.product_findings
    def uncertain(*args):
        local,item=original(*args)
        focus=next(c for c in local if c.identifier.endswith('.blur'))
        focus.status='Manual Review';focus.reason_code='calibration_boundary'
        return local,item
    monkeypatch.setattr(auto,'product_findings',uncertain)
    result=auto.inspect_auto(audit_image('small'))
    assert checks(result)['photo.blur'].status=='Manual Review'
    monkeypatch.setattr(auto,'product_findings',original)
    parent=auto.inspect_auto(audit_image('small'));before=parent.to_json()
    child=auto.amend_inspection(record(parent),points=[[30,30],[150,30],[150,160],[30,160]])
    assert checks(child)['photo.blur'].status=='Manual Review'
    assert 'assessment_scope' not in checks(child)['photo.blur'].measurement
    assert parent.to_json()==before


def test_localization_runs_segmentation_once_and_reuses_only_masks(normal_image,unit_policy,monkeypatch):
    count=0;original=auto.segment
    def segment(rgb):
        nonlocal count
        count+=1;return original(rgb)
    monkeypatch.setattr(auto,'segment',segment)
    raw=png_bytes(normal_image)
    first=auto.inspect_auto(raw,calibration=unit_policy)
    changed=copy.deepcopy(unit_policy);changed['score_verdict']={'pass_threshold':101}
    second=auto.inspect_auto(raw,calibration=changed)
    assert count==1
    assert not first.segmentation['timings']['localization_cache_hit']
    assert second.segmentation['timings']['localization_cache_hit']
    assert first.status=='Pass' and second.status=='Needs Review'
    assert first.mask_png==second.mask_png and first.inspection_id!=second.inspection_id


def test_cache_is_bounded_and_returns_independent_arrays(normal_image,monkeypatch,tmp_path):
    monkeypatch.setattr(auto,'CHECKPOINT',tmp_path)
    masks,coverage,seg,hit=auto.localize(normal_image,'fixture')
    assert not hit
    masks[0][:]=False;coverage['reason']='Changed caller data'
    again,meta,_,hit=auto.localize(normal_image,'fixture')
    assert hit and again[0].any() and meta['reason']!='Changed caller data'
    (tmp_path/'config.json').write_text('{}')
    assert not auto.localize(normal_image,'fixture')[-1]
    for index in range(5):auto.localize(normal_image,str(index))
    assert len(auto.LOCALIZATION_CACHE)<=4
    monkeypatch.setattr(auto,'LOCALIZATION_CACHE_BYTES',1)
    assert not auto.localize(normal_image,'oversized')[-1]
    assert not auto.localize(normal_image,'oversized')[-1]


def test_processing_duration_includes_all_three_evidence_encodings(normal_image,unit_policy,monkeypatch):
    clock=[0.];original=auto.evidence_png
    def encode(*args):
        clock[0]+=.1
        return original(*args)
    monkeypatch.setattr(auto.time,'perf_counter',lambda:clock[0])
    monkeypatch.setattr(auto,'evidence_png',encode)
    result=auto.inspect_auto(png_bytes(normal_image),calibration=unit_policy)
    assert result.duration_ms==pytest.approx(300)
    assert result.segmentation['timings']['encoding_ms']==pytest.approx(300)
    assert 'Processing time' in modern_ui.render_result(record(result))


def test_primary_advice_is_beside_verdict_and_bound_to_existing_finding():
    result=auto.inspect_auto(audit_image('merged'))
    html=modern_ui.render_result(record(result))
    overview=html.split('<div class="review-content">',1)[0]
    assert 'class="primary-suggestion"' in overview
    assert 'href="#coverage-question"' in overview
    assert 'data-finding-id="photo.coverage"' in overview
    approved=narration.approved_wording(result.to_dict())
    assert narration.validate({'items':approved},approved)==approved
    bad=copy.deepcopy(approved);bad[0]['statement']='All products are verified.'
    with pytest.raises(ValueError,match='unsupported_claim'):narration.validate({'items':bad},approved)


def test_http_timing_includes_commit_and_cached_uploads_still_create_fresh_reports(server,normal_image,unit_policy,monkeypatch):
    monkeypatch.setattr(auto,'policy',lambda:unit_policy)
    for index in range(2):
        identity=str(uuid4())
        body,kind=multipart({'submission_id':identity,'one_click':'yes'},png_bytes(normal_image))
        code,headers,_=request(server,'POST','/inspect',body,{'Content-Type':kind})
        assert code==303
        timing=dict((part.strip().split(';dur=')[0],float(part.split(';dur=')[1])) for part in headers['Server-Timing'].split(','))
        assert timing['request']+.1>=timing['processing']+timing['storage']
        saved=json.loads(load_inspection(identity,server.database)['result_json'])
        assert saved['segmentation']['timings']['localization_cache_hit']==bool(index)
        assert saved['duration_ms']>=saved['segmentation']['timings']['encoding_ms']
