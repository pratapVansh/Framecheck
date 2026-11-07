"""Regression fixtures prove audit mechanisms; they are not calibration data."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from inspector import auto_inspect, modern_ui, narration
from inspector.auto_inspect import amend_inspection, inspect_auto, photo_findings, policy, product_findings, signal
from inspector.images import png_bytes
from inspector.auto_inspect import CheckResult, aggregate_required as aggregate
from inspector.models import Status


ROOT = Path(__file__).resolve().parents[1]


def audit_image(case):
    rects={'separated':[(160,200,250,220),(490,200,250,220)],
           'merged':[(160,200,250,220),(410,200,250,220)], 'missed':[(160,200,250,220)],
           'clipped':[(160,200,250,220),(490,200,250,220)],
           'corner':[(8,8,300,220)], 'small':[(390,250,120,120)]}
    rgb=np.full((640,900,3),245,np.uint8)
    for x,y,w,h in rects[case]:
        yy,xx=np.indices((h,w))
        rgb[y:y+h,x:x+w]=(20+xx%80+80*((yy//2)%2)).astype(np.uint8)[:,:,None]
    if case=='missed':rgb[210:410,530:730]=240
    if case=='clipped':rgb[200:420,490:665]=255
    return png_bytes(rgb)


def record(result):
    return {'result_json': result.to_json(), 'original_png': result.original_png,
            'mask_png': result.mask_png, 'overlay_png': result.overlay_png, 'filename': 'fixture.png'}


def checks(result):
    return {c.identifier: c for c in result.checks}


@pytest.fixture
def source():
    return audit_image('separated')


@pytest.fixture
def scoped_policy():
    value = copy.deepcopy(policy())
    value['composition'].update(min_coverage=.20, max_coverage=.95, max_center_offset=.25, min_margin=.005)
    return value


def test_exposure_percentiles_use_only_the_selected_mask():
    rgb = np.full((640, 900, 3), 180, np.uint8)
    mask = np.zeros(rgb.shape[:2], bool)
    mask[200:420, 200:450] = True
    rgb[mask] = 2
    local = signal(rgb, mask)
    assert local['shadow_loss'] == 6
    assert signal(rgb)['shadow_loss'] == 0
    rgb[~mask] = 0
    assert signal(rgb, mask)['shadow_loss'] == local['shadow_loss']
    rgb[mask] = 254
    assert signal(rgb, mask)['highlight_loss'] == 7
    assert signal(rgb, np.zeros(mask.shape, bool))['highlight_loss'] is None


@pytest.mark.parametrize('case,count', [('separated', 2), ('merged', 1), ('missed', 1)])
def test_foreground_accounting_is_not_intended_product_confirmation(case, count):
    result = inspect_auto(audit_image(case))
    assert len(result.segmentation['products']) == count
    by_id = checks(result)
    assert by_id['photo.coverage'].status == Status.REVIEW
    assert not result.segmentation['coverage']['intended_complete']
    assert 'photo.foreground_coverage' in by_id
    assert all(p['finding_ids'] for p in result.segmentation['products'])
    if case == 'missed':
        assert by_id['photo.foreground_coverage'].status == Status.REVIEW
        assert result.segmentation['coverage']['suspected_missing_regions'] > 0


def test_incomplete_mask_stays_review_after_intended_items_confirmed(monkeypatch):
    # Hold accepted masks fixed to test mask warnings without the optional clothing parser.
    masks=[]
    for left,right in [(160,410),(665,740)]:
        mask=np.zeros((640,900),bool);mask[200:420,left:right]=True;masks.append(mask)
    monkeypatch.setattr(auto_inspect,'detect',lambda image:(masks,{
        'method':'test-fixture','coverage_complete':True,'reason':'Fixed accepted regions','disclosure':'Procedural fixture'}))
    result = inspect_auto(audit_image('clipped'))
    assert checks(result)['product-2.mask_completeness'].status == Status.REVIEW
    child = amend_inspection(record(result), confirm_coverage=True)
    assert checks(child)['photo.coverage'].status == Status.PASS
    assert checks(child)['product-2.mask_completeness'].status == Status.REVIEW
    assert not child.segmentation['products'][1]['mask_verified']
    assert child.status == Status.REVIEW


@pytest.mark.parametrize('luminance', [0, 255])
def test_natural_extreme_colors_request_review_not_a_confirmed_exposure_failure(luminance):
    rgb = np.full((640, 900, 3), 180, np.uint8)
    mask = np.zeros(rgb.shape[:2], bool)
    mask[200:420, 200:450] = True
    rgb[mask] = luminance
    local, _ = product_findings(rgb, mask, 1, policy())
    by_id = {c.identifier: c for c in local}
    assert by_id['product-1.contrast'].status == Status.REVIEW
    assert by_id['product-1.clipping'].status == Status.REVIEW
    assert all(c.status != Status.FAIL for c in local)


def test_local_clipping_is_visible_even_when_whole_photo_exposure_passes(source):
    from inspector.images import decode_image
    rgb, _ = decode_image(source)
    mask = np.zeros(rgb.shape[:2], bool)
    mask[200:420, 490:740] = True
    rgb[200:420, 490:715] = 255
    local, _ = product_findings(rgb, mask, 2, policy())
    assert next(c for c in local if c.identifier.endswith('.clipping')).status == Status.REVIEW
    photo, _ = photo_findings(rgb, policy())
    assert next(c for c in photo if c.identifier == 'photo.highlight_loss').status == Status.PASS


def test_blank_background_does_not_fail_contrast_and_small_group_is_reported():
    result = inspect_auto(audit_image('small'))
    by_id = checks(result)
    assert by_id['photo.contrast'].status == Status.REVIEW
    assert by_id['photo.contrast'].reason_code == 'background_dominated_contrast'
    assert by_id['photo.group_size'].reason_code == 'small_in_frame'
    assert by_id['photo.noise'].status == Status.PASS
    assert by_id['photo.highlight_loss'].status == Status.PASS
    assert result.status == Status.REVIEW


def test_group_placement_and_margins_use_native_coordinates():
    result = inspect_auto(audit_image('corner'))
    by_id = checks(result)
    assert by_id['photo.group_margins'].measurement['margins'][0] == pytest.approx(8 / 900)
    assert by_id['photo.group_placement'].status == Status.REVIEW
    child = amend_inspection(record(result), confirm_coverage=True)
    assert checks(child)['photo.group_placement'].status == Status.FAIL
    assert checks(child)['photo.group_margins'].status == Status.FAIL


def test_exclusion_preserves_observations_and_recalculates_group(source, scoped_policy):
    parent = inspect_auto(source, calibration=scoped_policy)
    saved = record(parent)
    child = amend_inspection(saved, intended_numbers=[1], confirm_coverage=True)
    assert saved['result_json'] == parent.to_json()
    assert len(child.segmentation['products'])==1
    assert not child.segmentation['incidental_regions'][0]['intended']
    old = checks(parent)
    assert all(c.status == old[c.identifier].status and not c.required
               for c in child.checks if c.identifier.startswith('product-2.'))
    assert checks(child)['photo.group_size'].measurement['bbox'] == parent.segmentation['products'][0]['bbox']
    restored = amend_inspection(record(child), intended_numbers=[1, 2], confirm_coverage=True)
    assert all(c.required for c in restored.checks if c.identifier.startswith('product-2.'))
    assert all(not p['mask_verified'] for p in restored.segmentation['products'])
    for invalid in [[], [999], [True], [1, 1], ['1']]:
        with pytest.raises(ValueError):
            amend_inspection(saved, intended_numbers=invalid, confirm_coverage=True)


def test_missing_composition_policy_requests_review(source, scoped_policy):
    scoped_policy.pop('composition')
    result = inspect_auto(source, calibration=scoped_policy)
    child = amend_inspection(record(result), confirm_coverage=True)
    assert checks(child)['photo.group_size'].reason_code == 'composition_policy_unavailable'


def test_real_single_product_can_pass_after_intent_confirmation():
    source = ROOT / 'reports/multi-product-debug/browser-verified/shoe/original.png'
    if not source.is_file():
        pytest.skip('Optional local user-photo regression; private photos are not distributed.')
    raw = source.read_bytes()
    parent = inspect_auto(raw)
    assert parent.status == Status.REVIEW
    child = amend_inspection(record(parent), confirm_coverage=True)
    assert child.status == Status.PASS
    assert len(child.segmentation['products']) == 1


def test_single_product_flow_without_private_photos_or_model():
    rgb=np.full((640,900,3),245,np.uint8)
    y,x=np.indices((280,500))
    rgb[180:460,200:700]=(20+x%80+80*((y//2)%2)).astype(np.uint8)[:,:,None]
    parent=inspect_auto(png_bytes(rgb))
    assert len(parent.segmentation['products'])==1
    assert parent.segmentation['coverage']['method']=='general_foreground'
    assert parent.status==Status.REVIEW
    child=amend_inspection(record(parent),confirm_coverage=True)
    assert child.status==Status.PASS
    assert not child.segmentation['products'][0]['mask_verified']


def test_noise_and_exposure_failures_survive_missing_or_excluded_products(source, monkeypatch):
    from inspector.images import decode_image
    rgb, _ = decode_image(source)
    masks = []
    for x in [160, 490]:
        mask = np.zeros(rgb.shape[:2], bool)
        mask[200:420, x:x+250] = True
        masks.append(mask)
    union = np.logical_or(*masks)
    rng = np.random.default_rng(74)
    rgb[~union] = np.clip(rgb[~union].astype(float) + rng.normal(0, 35, rgb[~union].shape), 0, 255)
    monkeypatch.setattr(auto_inspect, 'detect', lambda image: (masks, {
        'method': 'test', 'coverage_complete': True, 'reason': 'Fixed original masks', 'disclosure': 'Fixture'}))
    parent = inspect_auto(png_bytes(rgb))
    assert checks(parent)['photo.noise'].status == Status.FAIL
    child = amend_inspection(record(parent), intended_numbers=[1], confirm_coverage=True)
    assert child.status == Status.FAIL
    assert checks(child)['photo.noise'].to_dict() == checks(parent)['photo.noise'].to_dict()
    assert aggregate([CheckResult('advisory', 'quality', Status.FAIL, {}, {}, '', required=False)]) == Status.REVIEW


def test_all_findings_render_and_groq_cannot_override_new_evidence(source):
    result = inspect_auto(source)
    report = result.to_dict()
    approved = narration.approved_wording(report)
    assert len(narration.validate({'items': approved}, approved)) == len(result.checks)
    bad = copy.deepcopy(approved)
    next(item for item in bad if item['finding_id'] == 'photo.coverage')['statement'] = 'Every product is verified.'
    with pytest.raises(ValueError, match='unsupported_claim'):
        narration.validate({'items': bad}, approved)
    rendered = modern_ui.render_result(record(result))
    assert 'intended_1' in rendered and 'intended_2' in rendered
    assert 'does not verify' in rendered
    assert all(f'data-finding-id="{c.identifier}" data-statement' in rendered for c in result.checks)
    assert 'photo.group_size' in rendered and 'product-1.clipping' in rendered


def test_group_can_pass_with_two_individually_off_center_products(source):
    parent=inspect_auto(source)
    child=amend_inspection(record(parent),confirm_coverage=True)
    assert child.status==Status.PASS
    assert all(c.status==Status.PASS for c in child.checks if c.identifier.startswith('photo.group_'))
    assert all(abs((p['bbox'][0]+p['bbox'][2]/2)/child.width-.5)>.1 for p in child.segmentation['products'])


def test_raw_small_components_are_disclosed_before_cleanup_can_hide_them(source):
    from inspector.images import decode_image
    rgb,_=decode_image(source)
    rgb[500:520,650:670]=40
    result=inspect_auto(png_bytes(rgb))
    assert result.segmentation['coverage']['discarded_raw_pixels']>0
    assert checks(result)['photo.foreground_coverage'].status==Status.REVIEW
    assert checks(result)['photo.coverage'].status==Status.REVIEW


def test_provider_contract_covers_new_ids_without_changing_report(source,monkeypatch,tmp_path):
    from test_narration import response_for
    result=inspect_auto(source)
    report=result.to_dict();before=copy.deepcopy(report)
    monkeypatch.setattr(narration,'ENV_FILE',tmp_path/'absent.env')
    monkeypatch.setenv('GROQ_API_KEY','fixture-key')
    def provider(request,timeout):
        body=json.loads(request.data)
        approved=json.loads(body['messages'][1]['content'])['approved_items']
        ids={item['finding_id'] for item in approved}
        assert {'photo.group_size','photo.foreground_coverage','product-1.clipping','product-1.mask_completeness'}<=ids
        score_text=json.loads(body['messages'][1]['content'])['approved_score_explanation']
        return response_for({'items':approved,'score_explanation':score_text})
    monkeypatch.setattr(narration.urllib.request,'urlopen',provider)
    narration._explain.cache_clear()
    try:
        output=narration.explain(report)
        assert output['source']=='groq_validated'
        assert len(output['items'])==len(result.checks)
        assert report==before
    finally:narration._explain.cache_clear()
