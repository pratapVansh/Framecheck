"""Regression proof for product scope and reference-mask save failures."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from inspector import web
from inspector.benchmark import read_manifest, rules_hash, summarize_predictions, write_manifest
from inspector.images import png_bytes
from inspector.models import DEFECTS, InspectionProfile, Status
from inspector.pipeline import inspect_image, measure_image
from scripts.calibrate import predict


def test_missing_photo_context_requires_review(product_bytes):
    result = inspect_image(product_bytes)
    assert result.status == Status.REVIEW
    assert result.photo_context == "uncertain"
    assert all(c.status == Status.REVIEW for c in result.checks if c.identifier != "low_resolution")


@pytest.mark.parametrize("context", ["model_worn", "uncertain"])
def test_person_bounds_cannot_produce_product_layout_failure(product, context):
    # A foreground touching the frame is not proof that a worn garment is cropped.
    product[68:, 15:45] = product[65:66, 15:45]
    result = inspect_image(png_bytes(product), photo_context=context)
    assert result.status == Status.REVIEW
    assert not result.segmentation["product_scope_confirmed"]
    assert not result.segmentation["reliable"]
    for check in result.checks:
        if check.identifier != "low_resolution":
            assert check.status == Status.REVIEW
            assert "context" in check.explanation.lower()


def test_uncertain_scope_keeps_resolution_failure(product):
    result = inspect_image(png_bytes(cv2.resize(product, (30, 40))), photo_context="model_worn")
    assert result.status == Status.FAIL
    assert next(c for c in result.checks if c.identifier == "low_resolution").status == Status.FAIL
    assert all(c.status == Status.REVIEW for c in result.checks if c.identifier != "low_resolution")


def test_invalid_photo_context_rejected(product_bytes):
    with pytest.raises(ValueError, match="photo context|Photo context"):
        inspect_image(product_bytes, photo_context="automatically_trusted")


def test_legacy_saved_results_are_visible_with_warning(product_bytes):
    result = inspect_image(product_bytes, photo_context="isolated_product").to_dict()
    result.pop("photo_context")
    result["pipeline_version"] = "0.1.0"
    import json
    rendered = web.render_result({"result_json": json.dumps(result), "filename": "legacy.png"})
    assert "predates the product-photo context safeguard" in rendered
    assert "Unspecified (legacy inspection)" in rendered


def test_offline_prediction_uses_each_images_context(product_bytes):
    measured = measure_image(product_bytes)
    rows = [{"photo_context": "isolated_product"}, {"photo_context": "model_worn"}, {}]
    predictions = predict([measured] * 3, InspectionProfile(), rows)
    assert [p["status"] for p in predictions] == [Status.PASS, Status.REVIEW, Status.REVIEW]


def prepare_review(tmp_path, monkeypatch, product_bytes):
    directory = tmp_path / "review-data"
    directory.mkdir()
    monkeypatch.setattr(web, "DATA", directory)
    image = directory / "product.png"
    image.write_bytes(product_bytes)
    masks = directory / "masks"
    masks.mkdir()
    old_mask = masks / (web.hashlib_id("unit-product") + ".png")
    pixels = np.zeros((80, 60), np.uint8)
    pixels[15:60, 15:45] = 255
    old_mask.write_bytes(png_bytes(pixels))
    row = {"id": "unit-product", "source_id": "unit-product", "group_id": "unit-product",
           "image": str(image), "split": None, "labels": dict.fromkeys(DEFECTS, False),
           "reviewed": False, "rules_hash": rules_hash(InspectionProfile()),
           "mask": str(old_mask), "mask_reviewed": True, "photo_context": "isolated_product"}
    manifest = directory / "manifest.jsonl"
    write_manifest(manifest, [row])
    fields = {"manifest": str(manifest), "index": "0", "reviewer": "unit-test reviewer",
              "confirmed": "yes", "group_id": "unit-product", "photo_context": "isolated_product",
              "mask_confirm": "yes", "polygon": "[[10,10],[50,10],[50,70],[10,70]]"}
    fields.update({"label-" + defect: "absent" for defect in DEFECTS})
    return manifest, old_mask, fields


class ReviewHandler:
    def redirect(self, path):
        return path


def test_invalid_review_cannot_overwrite_saved_mask(tmp_path, monkeypatch, product_bytes):
    manifest, old_mask, fields = prepare_review(tmp_path, monkeypatch, product_bytes)
    previous_mask, previous_manifest = old_mask.read_bytes(), manifest.read_bytes()
    fields["group_id"] = "   "
    with pytest.raises(ValueError, match="group cannot be empty"):
        web.Handler.save_review(ReviewHandler(), fields, {})
    assert old_mask.read_bytes() == previous_mask
    assert manifest.read_bytes() == previous_manifest
    assert set(old_mask.parent.iterdir()) == {old_mask}


def test_manifest_failure_preserves_previous_mask_and_annotation(tmp_path, monkeypatch, product_bytes):
    manifest, old_mask, fields = prepare_review(tmp_path, monkeypatch, product_bytes)
    previous_mask, previous_manifest = old_mask.read_bytes(), manifest.read_bytes()
    def failed_commit(*args):
        raise OSError("simulated manifest commit failure")
    monkeypatch.setattr(web, "write_manifest", failed_commit)
    with pytest.raises(OSError, match="simulated manifest commit failure"):
        web.Handler.save_review(ReviewHandler(), fields, {})
    assert old_mask.read_bytes() == previous_mask
    assert manifest.read_bytes() == previous_manifest
    assert set(old_mask.parent.iterdir()) == {old_mask}


def test_successful_mask_edit_versions_evidence(tmp_path, monkeypatch, product_bytes):
    manifest, old_mask, fields = prepare_review(tmp_path, monkeypatch, product_bytes)
    previous_mask = old_mask.read_bytes()
    web.Handler.save_review(ReviewHandler(), fields, {})
    saved = read_manifest(manifest)[0]
    assert saved["reviewed"] and saved["photo_context"] == "isolated_product"
    assert saved["mask_reviewed"] and Path(saved["mask"]) != old_mask
    assert old_mask.read_bytes() == previous_mask
    assert Path(saved["mask"]).read_bytes() != previous_mask


def test_invalid_review_context_preserves_existing_annotation(tmp_path, monkeypatch, product_bytes):
    manifest, old_mask, fields = prepare_review(tmp_path, monkeypatch, product_bytes)
    previous_mask, previous_manifest = old_mask.read_bytes(), manifest.read_bytes()
    fields["photo_context"] = "trusted_without_review"
    with pytest.raises(ValueError, match="Photo context"):
        web.Handler.save_review(ReviewHandler(), fields, {})
    assert old_mask.read_bytes() == previous_mask
    assert manifest.read_bytes() == previous_manifest


def test_cropping_referrals_do_not_become_primary_detections():
    row = {"id": "unit-product", "group_id": "unit-product", "labels": dict.fromkeys(DEFECTS, False)}
    row["labels"]["possible_cropping"] = True
    prediction = {"status": Status.REVIEW, "checks": dict.fromkeys(DEFECTS, Status.PASS)}
    prediction["checks"]["possible_cropping"] = Status.REVIEW
    metrics = summarize_predictions([row], [prediction])
    assert metrics["defect_recall"] == 0
    assert metrics["per_category"]["possible_cropping"]["f1"] == 0
    assert metrics["advisory_cropping"]["true_cases_referred"] == 1
    assert metrics["advisory_cropping"]["referral_recall"] == 1
