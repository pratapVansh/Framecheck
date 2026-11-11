"""Contracts for correction provenance, visible blockers, and lossless evidence reuse."""
import copy
import json
from io import BytesIO
from uuid import uuid4

import numpy as np
from PIL import Image
import pytest

from inspector import auto_inspect as auto, modern_ui, narration
from inspector.storage import save_inspection
from inspector.story import friendly_finding
from test_one_click import normal_image, unit_policy, record
from test_web import server, request, multipart


def test_historical_corrections_keep_measurement_and_profile_versions(normal_image, unit_policy):
    parent = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy)
    saved = record(parent)
    historical = json.loads(saved['result_json'])
    historical['pipeline_version'] = '1.5.0-auto-coverage-dip'
    historical['segmentation'].pop('provenance', None)
    saved['result_json'] = json.dumps(historical)
    before = copy.deepcopy(saved)
    child = auto.amend_inspection(saved, confirm_coverage=True)
    assert child.pipeline_version == historical['pipeline_version']
    assert child.profile == historical['profile']
    provenance = child.segmentation['provenance']
    assert provenance['operation'] == 'correction'
    assert provenance['measurement_version'] == historical['pipeline_version']
    assert provenance['profile_version'] == historical['profile']['feature_version']
    assert provenance['correction_version'] == provenance['scoring_version'] == auto.PIPELINE_VERSION
    grandchild = auto.amend_inspection(record(child), confirm_coverage=True)
    assert grandchild.segmentation['provenance']['measurement_version'] == historical['pipeline_version']
    assert grandchild.segmentation['provenance']['profile_sha256'] == provenance['profile_sha256']
    html = modern_ui.render_result({**record(child), 'filename': 'historical.png'})
    assert 'Corrected selection' in html and 'Saved measurements retained' in html
    assert 'Reprocessed original' not in html
    assert saved == before


def test_marking_records_mixed_measurement_versions(normal_image, unit_policy):
    parent = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy)
    saved = record(parent)
    old = json.loads(saved['result_json']); old['pipeline_version'] = '1.5.0-auto-coverage-dip'
    old['segmentation'].pop('provenance', None); saved['result_json'] = json.dumps(old)
    child = auto.amend_inspection(saved, points=[[20, 20], [150, 20], [150, 180], [20, 180]])
    assert child.pipeline_version == old['pipeline_version']
    assert child.segmentation['provenance']['added_region_measurements'][-1]['measurement_version'] == auto.PIPELINE_VERSION
    assert child.profile == old['profile']


def test_legacy_derived_report_does_not_invent_measurement_origin(normal_image, unit_policy):
    parent = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy)
    parent.segmentation.pop('provenance')
    parent.segmentation['source_inspection_id'] = str(uuid4())
    child = auto.amend_inspection(record(parent), confirm_coverage=True)
    assert child.segmentation['provenance']['measurement_version'] is None
    legacy_html = modern_ui.render_result({**record(parent), 'filename': 'legacy-child.png'})
    assert 'Historical derived report' in legacy_html
    assert 'measurement origin was not recorded' in legacy_html


def test_missing_product_finding_is_visible_as_incomplete(normal_image, unit_policy):
    parent = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy)
    parent.checks = [c for c in parent.checks if c.identifier != 'product-1.clipping']
    html = modern_ui.render_result({**record(parent), 'filename': 'missing.png'})
    card = html.split('<article class="product-card"', 1)[1].split('</article>', 1)[0]
    assert 'data-blocking-finding="product-1.clipping"' in card
    assert 'This part of the review did not finish' in card
    assert 'Needs a closer look' in card
    primary = html.split('class="primary-suggestion"', 1)[1].split('</section>', 1)[0]
    assert 'action="/reinspect"' in primary


def test_only_reinspection_claims_fresh_measurements(server, normal_image, unit_policy, monkeypatch):
    monkeypatch.setattr(auto, 'policy', lambda: unit_policy)
    parent = auto.inspect_auto(auto.png_bytes(normal_image))
    parent.pipeline_version = '1.5.0-auto-coverage-dip'
    parent.segmentation.pop('provenance', None)
    save_inspection(parent, 'historical.png', server.database)
    raw = request(server, 'GET', f'/report/{parent.inspection_id}.json')[2]
    child_id = str(uuid4())
    body, kind = multipart({'source_inspection_id': parent.inspection_id, 'submission_id': child_id})
    assert request(server, 'POST', '/reinspect', body, {'Content-Type': kind})[0] == 303
    child = json.loads(request(server, 'GET', f'/report/{child_id}.json')[2])
    assert child['pipeline_version'] == auto.PIPELINE_VERSION
    assert child['segmentation']['provenance']['operation'] == 'reinspection'
    assert child['segmentation']['provenance']['measurement_version'] == auto.PIPELINE_VERSION
    html = request(server, 'GET', f'/inspection/{child_id}')[2].decode()
    assert 'Reprocessed original' in html and 'Corrected selection' not in html
    assert request(server, 'GET', f'/report/{parent.inspection_id}.json')[2] == raw


@pytest.mark.parametrize('key', ['contrast', 'clipping', 'mask_completeness'])
@pytest.mark.parametrize('status', ['Manual Review', 'Disabled'])
def test_hidden_product_review_is_visible_and_grounded(normal_image, unit_policy, key, status):
    result = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy)
    finding = next(c for c in result.checks if c.identifier == f'product-1.{key}')
    finding.status = status; finding.explanation = 'This exact fixture check is incomplete.'
    html = modern_ui.render_result({**record(result), 'filename': 'fixture.png'})
    card = html.split('<article class="product-card"', 1)[1].split('</article>', 1)[0]
    assert f'data-blocking-finding="{finding.identifier}"' in card
    assert friendly_finding(finding.to_dict()) in card and 'Needs a closer look' in card
    assert 'Needs a closer look' in card
    assert f'href="#evidence-{finding.identifier}"' in card
    overview = html.split('<details class="review-details"', 1)[0]
    assert 'Your photo review' in overview and 'Whole-photo quality' not in overview
    assert 'points deducted' not in overview and 'points pending' not in overview
    assert 'Confirmed problems' in html and 'Incomplete assessment' in html
    approved = narration.approved_wording(result.to_dict())
    assert narration.validate({'items': approved}, approved) == approved


def test_same_check_with_fail_and_review_does_not_share_a_confirmed_claim(normal_image, unit_policy):
    result = auto.inspect_auto(auto.png_bytes(normal_image), calibration=unit_policy)
    for identity, status in [('photo.blur', 'Fail'), ('product-1.blur', 'Manual Review')]:
        check = next(c for c in result.checks if c.identifier == identity)
        check.status = status
    html = modern_ui.render_result({**record(result), 'filename': 'fixture.png'})
    assert html.count('data-action-kind="confirmed"') >= 1
    assert html.count('data-action-kind="incomplete"') >= 1


def test_evidence_cache_reuses_only_identical_pixels_and_masks(normal_image, unit_policy, monkeypatch):
    auto.EVIDENCE_CACHE.clear()
    calls = []; original = auto.evidence_png
    def encode(array):
        calls.append(array.shape)
        return original(array)
    monkeypatch.setattr(auto, 'evidence_png', encode)
    raw = auto.png_bytes(normal_image)
    first = auto.inspect_auto(raw, calibration=unit_policy)
    second = auto.inspect_auto(raw, calibration=unit_policy)
    assert len(calls) == 3
    assert not first.segmentation['timings']['evidence_cache_hit']
    assert second.segmentation['timings']['evidence_cache_hit']
    assert first.original_png == second.original_png
    assert first.mask_png == second.mask_png and first.overlay_png == second.overlay_png
    assert first.score() == second.score()
    mask = np.zeros(normal_image.shape[:2], bool); mask[210:430, 280:430] = True
    monkeypatch.setattr(auto, 'detect', lambda rgb, **kwargs: ([mask], {
        'method': 'test-fixture', 'coverage_complete': True, 'reason': 'Changed mask', 'disclosure': 'Fixture only'}))
    changed = auto.inspect_auto(raw, calibration=unit_policy)
    assert len(calls) == 6 and changed.mask_png != first.mask_png
    assert not changed.segmentation['timings']['evidence_cache_hit']


def test_fast_evidence_is_lossless_and_cache_is_bounded(normal_image):
    blob = auto.evidence_png(normal_image)
    np.testing.assert_array_equal(np.asarray(Image.open(BytesIO(blob))), normal_image)
    auto.EVIDENCE_CACHE.clear()
    mask = np.zeros(normal_image.shape[:2], bool); mask[200:400, 300:600] = True
    for i in range(5):
        auto.encode_evidence(normal_image, [mask], str(i))
    assert len(auto.EVIDENCE_CACHE) == 4
    assert sum(sum(map(len, value)) for value in auto.EVIDENCE_CACHE.values()) <= auto.LOCALIZATION_CACHE_BYTES
