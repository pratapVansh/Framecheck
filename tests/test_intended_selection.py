"""Selection regressions are behavior checks, not product-identity calibration."""
import copy
import json
from uuid import uuid4

import pytest

from inspector import auto_inspect as auto, modern_ui
from inspector.scoring import score_statement
from inspector.storage import save_inspection
from test_photo_scope import audit_image, checks, record
from test_score_intent import scene
from test_web import server, request, multipart


@pytest.mark.parametrize('turns', range(4))
@pytest.mark.parametrize('labels', [('Top', 'Trousers'), ('Box', 'Bag')])
def test_attached_partial_item_is_context_in_every_orientation(turns, labels):
    # Match the audited portrait's relative areas; rotate to avoid a bottom/category rule.
    boxes = [[220, 210, 560, 450], [300, 650, 400, 350]]
    for _ in range(turns):
        boxes = [[1000-y-h, x, h, w] for x, y, w, h in boxes]
    products = []
    findings = []
    for number, (box, area, label) in enumerate(zip(boxes, [175286, 102942], labels), 1):
        ids = [f'product-{number}.edge', f'product-{number}.mask_completeness']
        products.append({'number': number, 'bbox': box, 'visible_pixels': area,
                         'label': label, 'finding_ids': ids})
        findings.extend([auto.finding(ids[0], 'Pass' if number == 1 else 'Manual Review',
                                      'Fixture edge evidence.', {'edge_contact': number == 2}),
                         auto.finding(ids[1], 'Pass', 'Fixture mask evidence.')])
    auto.select_intended(products, findings, 1000, 1000)
    assert [p['intended'] for p in products] == [True, False]
    assert products[1]['intent_evidence']['continuation_anchor'] == 1
    assert not findings[2].required and findings[2].status == 'Manual Review'


@pytest.mark.parametrize('rects', [
    [(300, 650, 450, 350)],  # A lone cropped product.
    [(220, 210, 560, 450), (290, 650, 560, 350)],  # Comparable-size cropped product.
    [(220, 210, 560, 450), (800, 650, 400, 350)],  # Separate cropped product.
    [(220, 210, 560, 450), (300, 620, 400, 350)],  # Complete second product.
    [(220, 210, 560, 450), (300, 600, 400, 400)],  # Substantial overlap, not a continuation.
])
def test_other_cropped_and_complete_products_remain_intended(monkeypatch, rects):
    parent = scene(monkeypatch, rects)
    assert len(parent.segmentation['products']) == len(rects)
    assert not parent.segmentation['incidental_regions']


def test_selection_groups_framing_but_keeps_focus_and_score_budgets():
    parent = auto.inspect_auto(audit_image('merged'))
    focus = checks(parent)['photo.blur']
    focus.status = 'Manual Review'
    focus.reason_code = 'calibration_boundary'
    before = copy.deepcopy(parent.to_dict())
    score = before['quality_score']
    framing = [c for c in score['components'] if c['component'].startswith('photo.group_')]
    assert all(c['blocked_by'] == ['photo.coverage'] for c in framing)
    assert sum(c['withheld'] for c in framing) == 10
    assert score['score'] + score['confirmed_deductions'] + score['uncertainty_withheld'] == 100
    assert 'photo.blur' in score['incomplete_checks']
    html = modern_ui.render_result(record(parent))
    overview, details = html.split('<details class="review-details"', 1)
    assert 'Change products (optional)' in overview
    assert overview.index('Change products (optional)') < overview.index('<figure class="review-photo">')
    assert f'href="/evidence/{parent.inspection_id}/original.png"' in overview
    assert 'Product selection and framing' in details
    assert '20 points remain pending for this shared selection blocker' in details
    assert 'Recenter the product group' not in overview
    assert 'Leave space around the products</h3>' not in overview
    assert 'photo focus' in score_statement(score)
    assert parent.to_dict() == before
    child = auto.amend_inspection(record(parent), confirm_coverage=True)
    assert checks(child)['photo.blur'].status == 'Manual Review'
    assert all(not c.get('blocked_by') for c in child.score()['components'])


def test_small_unconfirmed_group_shares_selection_blocker_without_a_retake_action():
    parent = auto.inspect_auto(audit_image('small'))
    parent.checks = [c for c in parent.checks if c.identifier not in
                     {'photo.group_size', 'photo.group_placement', 'photo.group_margins'}]
    checks(parent)['photo.coverage'].status = 'Manual Review'
    parent.checks.extend(auto.group_findings(parent.segmentation['products'], parent.width,
                                            parent.height, parent.profile, accepted=False))
    assert checks(parent)['photo.group_size'].reason_code == 'intended_group_unconfirmed'
    html = modern_ui.render_result(record(parent))
    overview = html.split('<details class="review-details"', 1)[0]
    assert 'Adjust the product framing' not in overview
    assert 'data-story-action="photo.coverage"' in overview


def test_optional_correction_restores_incidental_product_and_preserves_parent(server, monkeypatch):
    parent = scene(monkeypatch, [(220, 210, 560, 450), (300, 650, 400, 350)])
    assert parent.segmentation['product_count'] == 1
    save_inspection(parent, 'selection.png', server.database)
    raw = request(server, 'GET', f'/report/{parent.inspection_id}.json')[2]
    code, _, html = request(server, 'GET', f'/inspection/{parent.inspection_id}')
    assert code == 200
    overview = html.decode().split('<details class="review-details"', 1)[0]
    assert 'Change products (optional)' in overview
    child_id = str(uuid4())
    body, kind = multipart({'source_inspection_id': parent.inspection_id,
                            'submission_id': child_id, 'intent_selection': 'yes',
                            'intended_1': 'yes', 'intended_2': 'yes', 'coverage_confirm': 'yes'})
    assert request(server, 'POST', '/confirm-coverage', body, {'Content-Type': kind})[0] == 303
    child = json.loads(request(server, 'GET', f'/report/{child_id}.json')[2])
    assert child['segmentation']['product_count'] == 2
    assert child['segmentation']['coverage']['user_confirmed']
    assert next(c for c in child['checks'] if c['identifier'] == 'product-2.edge')['required']
    assert request(server, 'GET', f'/report/{parent.inspection_id}.json')[2] == raw
