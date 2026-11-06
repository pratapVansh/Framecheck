import json
from uuid import uuid4

import numpy as np
import pytest

from inspector import shirt_locator
from inspector.models import CheckResult, InspectionProfile, Status
from inspector.pipeline import inspect_image
from inspector.regions import region_measurements
from inspector.shirt_locator import apply_shirt_candidate, candidate_status, inspect_with_shirt_locator
from inspector.storage import load_inspection, list_inspections
from inspector.web import home
from test_web import multipart, request, server


def shirt_mask(rgb):
    mask = np.zeros(rgb.shape[:2], bool)
    mask[12:68, 15:45] = True
    return mask


@pytest.fixture(autouse=True)
def isolated_model_cache():
    shirt_locator._model.cache_clear()
    yield
    shirt_locator._model.cache_clear()


@pytest.mark.parametrize("kind,context,expected", [
    ("empty", "isolated_product", "empty"),
    ("full", "isolated_product", "ambiguous"),
    ("single", "uncertain", "ambiguous"),
    ("single", "model_worn", "candidate"),
    ("multiple", "isolated_product", "ambiguous"),
    ("tiny", "isolated_product", "insufficient_pixels"),
])
def test_candidate_states_do_not_claim_instance_reliability(product, kind, context, expected):
    mask = shirt_mask(product)
    if kind == "empty": mask[:] = False
    if kind == "full": mask[:] = True
    if kind == "multiple": mask[:, 28:32] = False
    if kind == "tiny": mask[:] = False; mask[30, 30] = True
    assert candidate_status(mask, product, context)[0] == expected
    with pytest.raises(ValueError, match="boolean mask"):
        candidate_status(mask.astype(np.uint8), product, context)


def test_empty_mask_does_not_hide_independent_resolution_failure(product, product_bytes):
    profile = InspectionProfile(min_short_side=200, min_long_side=200)
    baseline = inspect_image(product_bytes, profile, photo_context="model_worn")
    low_resolution = baseline.checks[0].to_dict()
    result = apply_shirt_candidate(baseline, product, np.zeros(product.shape[:2], bool), {})
    assert result.checks[0].to_dict() == low_resolution
    assert result.status == Status.FAIL
    assert all(c.status == Status.REVIEW and c.reason_code == "shirt_locator_empty" for c in result.checks[1:])
    assert result.segmentation["DIP_regions"]["whole_photo"]["usable_pixels"] > 32
    assert result.segmentation["DIP_regions"]["product"] is None
    assert result.segmentation["shirt_locator"]["verified_iou"] is None


def test_ambiguity_only_changes_declared_mask_checks(product, product_bytes):
    baseline = inspect_image(product_bytes, photo_context="uncertain")
    independent = CheckResult("whole_photo_example", "quality", Status.FAIL, {"score": 1}, {}, "Independent example")
    baseline.checks.append(independent)
    result = apply_shirt_candidate(baseline, product, shirt_mask(product), {})
    assert result.checks[-1] is independent and independent.status == Status.FAIL
    assert result.checks[0].status == Status.PASS and result.status == Status.FAIL
    assert all(c.reason_code == "shirt_locator_ambiguous" for c in result.checks[1:-1])


def test_usable_candidate_measures_product_but_does_not_reuse_legacy_thresholds(product, product_bytes):
    baseline = inspect_image(product_bytes, InspectionProfile(require_white=False), photo_context="model_worn")
    result = apply_shirt_candidate(baseline, product, shirt_mask(product), {"device": "cpu"})
    diagnostic = result.segmentation["DIP_regions"]
    assert diagnostic["product"]["usable_pixels"] > 32 and diagnostic["verdict"] is None
    assert result.checks[0].status == Status.PASS
    for check in result.checks[1:]:
        if check.identifier == "background_color":
            assert check.status == Status.DISABLED
        else:
            assert check.status == Status.REVIEW and check.reason_code == "experimental_shirt_uncalibrated"
            assert check.measurement["score"] is None
    assert result.status == Status.REVIEW
    assert not result.segmentation["reliable"] and not result.segmentation["shirt_locator"]["human_verified"]
    assert result.pipeline_version.endswith("+experimental-shirt-v1")


def test_whole_photo_diagnostics_are_mask_independent(product, product_bytes):
    left, right = shirt_mask(product), shirt_mask(product)
    left[:, 30:] = False
    right[:, :30] = False
    first = apply_shirt_candidate(inspect_image(product_bytes), product, left, {})
    second = apply_shirt_candidate(inspect_image(product_bytes), product, right, {})
    assert first.segmentation["DIP_regions"]["whole_photo"] == second.segmentation["DIP_regions"]["whole_photo"]
    assert first.checks[0].to_dict() == second.checks[0].to_dict()


def test_thin_images_keep_independent_diagnostics_instead_of_zero_size_resize():
    rgb = np.full((1, 600, 3), 128, np.uint8)
    result = region_measurements(rgb, None)
    assert result["native_width"] == 600 and result["native_height"] == 1
    assert result["whole_photo"]["reason"] == "insufficient_pixels"
    assert result["product"] is None


@pytest.mark.parametrize("failure", [ImportError("optional dependency missing"), RuntimeError("inference failed"), OSError("checkpoint unreadable")])
def test_model_failure_is_guarded_without_stopping_dip(monkeypatch, product_bytes, failure):
    def fail(): raise failure
    monkeypatch.setattr(shirt_locator, "load_shirt_model", lambda _: fail())
    result = inspect_with_shirt_locator(product_bytes, photo_context="isolated_product")
    assert result.checks[0].status == Status.PASS
    assert all(c.reason_code == "shirt_locator_unavailable" for c in result.checks[1:])
    assert result.segmentation["DIP_regions"]["whole_photo"]["usable_pixels"] > 32


def test_cached_loader_is_optional_and_default_dip_never_calls_it(monkeypatch, product_bytes):
    calls = []
    def load(_):
        calls.append("loaded")
        return shirt_mask, {"device": "cpu", "status": "loaded"}
    monkeypatch.setattr(shirt_locator, "load_shirt_model", load)
    baseline = inspect_image(product_bytes, photo_context="isolated_product")
    assert baseline.status == Status.PASS and calls == []
    for _ in range(2):
        result = inspect_with_shirt_locator(product_bytes, photo_context="isolated_product")
        assert result.segmentation["shirt_locator"]["status"] == "candidate"
    assert calls == ["loaded"]


def test_web_opt_in_persists_candidate_and_keeps_default_unselected(server, monkeypatch, product_bytes):
    monkeypatch.setattr(shirt_locator, "load_shirt_model", lambda _: (shirt_mask, {"device": "cpu"}))
    code, _, body = request(server, "GET", "/?advanced=1")
    assert code == 200 and b'<option value="auto" selected' in body
    assert b'<option value="segformer" selected>' not in body
    identity = str(uuid4())
    fields = {"submission_id": identity, "profile": "Demo White Background", "photo_context": "model_worn", "shirt_locator": "segformer"}
    form, content_type = multipart(fields, product_bytes)
    code, _, _ = request(server, "POST", "/inspect", form, {"Content-Type": content_type})
    assert code == 303
    code, _, body = request(server, "GET", f"/report/{identity}.json")
    report = json.loads(body)
    assert code == 200 and report["segmentation"]["shirt_locator"]["status"] == "candidate"
    assert report["checks"][0]["status"] == "Pass" and report["status"] == "Manual Review"
    assert request(server, "GET", f"/evidence/{identity}/mask.png")[0] == 200
    assert b"Product locator" in request(server, "GET", f"/inspection/{identity}")[2]


def test_web_rejects_unknown_locator_without_saving(server, product_bytes):
    form, content_type = multipart({"submission_id": str(uuid4()), "profile": "Demo White Background",
        "photo_context": "isolated_product", "shirt_locator": "guess"}, product_bytes)
    assert request(server, "POST", "/inspect", form, {"Content-Type": content_type})[0] == 400


def test_person_context_places_one_locator_before_submit_and_preserves_mode():
    from test_web import VisibleControls
    document = home(photo_context="model_worn", values={"shirt_locator": "segformer"})
    parser = VisibleControls()
    parser.feed(document)
    assert [(tag, attrs.get("name")) for tag, attrs in parser.controls] == [
        ("input", "image"), ("select", "product_category"), ("select", "photo_context"),
        ("select", "product_locator"), ("button", None)]
    assert document.count('name="product_locator"') == 1
    assert '<option value="segformer" selected' in document
    assert "categorySelect.addEventListener('change',updateProductLocator)" in document


def test_form_error_preserves_visible_locator_and_submitted_mode(server):
    form, kind = multipart({"submission_id": str(uuid4()), "profile": "Demo White Background",
        "photo_context": "model_worn", "shirt_locator": "segformer"}, b"not an image")
    code, _, body = request(server, "POST", "/inspect", form, {"Content-Type": kind})
    assert code == 400
    assert b'<option value="segformer" selected' in body
    assert body.index(b'id="product-locator-field"') < body.index(b'Check photo</button>')


@pytest.mark.parametrize("minimum,expected", [(60, "Manual Review"), (200, "Fail")])
def test_target_choice_preserves_source_and_independent_checks_without_inference(server, monkeypatch, product_bytes, minimum, expected):
    from io import BytesIO
    from PIL import Image
    calls = []
    def predict(rgb):
        calls.append(1)
        mask = shirt_mask(rgb)
        mask[:, 27:33] = False
        return mask
    monkeypatch.setattr(shirt_locator, "load_shirt_model", lambda _: (predict, {"device": "cpu"}))
    parent_id = str(uuid4())
    fields = {"submission_id": parent_id, "profile": "Demo White Background", "photo_context": "model_worn",
              "shirt_locator": "segformer", "min_short_side": str(minimum), "min_long_side": str(max(80, minimum))}
    form, kind = multipart(fields, product_bytes)
    assert request(server, "POST", "/inspect", form, {"Content-Type": kind})[0] == 303
    parent = load_inspection(parent_id, server.database)
    parent_json = json.loads(parent["result_json"])
    assert parent_json["segmentation"]["requested_locator"] == "segformer"
    assert len(parent_json["segmentation"]["shirt_locator"]["targets"]) == 2
    document = request(server, "GET", f"/inspection/{parent_id}")[2]
    assert b'Choose the intended product' in document and b'action="/select-shirt"' in document
    assert b'action="/select-shirt" enctype="multipart/form-data"' in document
    assert b'Status: <strong>Ambiguous</strong>' in document
    child_id = str(uuid4())
    choice, kind = multipart({"submission_id": child_id, "source_inspection_id": parent_id, "shirt_target": "2"})
    for _ in range(2):
        code, headers, _ = request(server, "POST", "/select-shirt", choice, {"Content-Type": kind})
        assert code == 303 and headers["Location"] == f"/inspection/{child_id}"
    assert calls == [1] and len(list_inspections(server.database)) == 2
    assert load_inspection(parent_id, server.database)["result_json"] == parent["result_json"]
    child = load_inspection(child_id, server.database)
    report = json.loads(child["result_json"])
    assert report["status"] == expected
    assert report["checks"][0] == parent_json["checks"][0]
    assert report["segmentation"]["DIP_regions"]["whole_photo"] == parent_json["segmentation"]["DIP_regions"]["whole_photo"]
    assert report["segmentation"]["DIP_regions"]["product"]["usable_pixels"] > 32
    locator = report["segmentation"]["shirt_locator"]
    assert locator["status"] == "selected" and locator["selected_target"] == 2
    assert locator["source_inspection_id"] == parent_id and not locator["inference_rerun"]
    assert locator["human_verified"] is False and locator["verified_iou"] is None
    assert all(c["reason_code"] == "experimental_shirt_uncalibrated" for c in report["checks"][1:])
    mask = np.asarray(Image.open(BytesIO(child["mask_png"]))) > 0
    assert not mask[:, :33].any() and mask[12:68, 33:45].all()
    assert report["pipeline_version"].count("+experimental-shirt-v1") == 1
    assert b'Status: <strong>Selected</strong>' in request(server, "GET", f"/inspection/{child_id}")[2]
    invalid, kind = multipart({"submission_id": str(uuid4()), "source_inspection_id": parent_id, "shirt_target": "999"})
    assert request(server, "POST", "/select-shirt", invalid, {"Content-Type": kind})[0] == 400
    assert len(list_inspections(server.database)) == 2
