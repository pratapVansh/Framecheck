from copy import deepcopy
import json

import numpy as np
import pytest

from inspector.benchmark import assert_group_splits, development_digest, image_bytes, read_manifest, rules_hash, summarize_predictions, write_manifest
from inspector.models import DEFECTS, InspectionProfile, Status
from scripts.generate import transform


def row(identity="1", split="test"):
    return {"id": identity, "source_id": identity, "group_id": identity, "image": "unused.png", "split": split,
            "labels": dict.fromkeys(DEFECTS, False), "reviewed": True, "rules_hash": rules_hash(InspectionProfile())}


def prediction(status=Status.PASS):
    return {"status": status, "checks": dict.fromkeys(DEFECTS, Status.PASS)}


def test_metric_accounting_keeps_reviews_and_unknowns():
    acceptable = row("acceptable")
    defective = row("defective")
    defective["labels"]["blur"] = True
    uncertain = row("uncertain")
    uncertain["labels"]["noise"] = None
    first, second, third = prediction(Status.FAIL), prediction(Status.REVIEW), prediction(Status.PASS)
    first["checks"]["noise"] = Status.FAIL
    second["checks"]["blur"] = Status.REVIEW
    metrics = summarize_predictions([acceptable, defective, uncertain], [first, second, third])
    assert metrics["false_reject_rate"] == 1
    assert metrics["defect_recall"] == 0
    assert metrics["manual_review_rate"] == pytest.approx(1 / 3)
    assert metrics["per_category"]["noise"]["assessed_support"] == 2
    assert metrics["per_category"]["blur"]["reviewed_true_defects"] == 1
    assert metrics["acceptable_images"] == 1


def test_macro_f1_uses_fixed_twelve_categories():
    actual = row()
    actual["labels"]["blur"] = True
    pred = prediction(Status.FAIL)
    pred["checks"]["blur"] = Status.FAIL
    metrics = summarize_predictions([actual], [pred])
    assert metrics["macro_f1"] == pytest.approx(1 / 12)
    assert metrics["defect_recall"] == 1
    assert metrics["false_reject_rate"] is None
    assert not metrics["complete_category_support"]


@pytest.mark.parametrize("key", ["group_id", "source_id", "source_hash"])
def test_leakage_detection(key):
    first, second = row("first", "train"), row("second", "test")
    first[key] = second[key] = "same"
    with pytest.raises(ValueError, match="leakage"):
        assert_group_splits([first, second])


def test_development_digest_ignores_test_and_order():
    train, val, test = row("train", "train"), row("val", "validation"), row("test", "test")
    original = development_digest([train, test, val])
    test["labels"]["noise"] = True
    assert development_digest([val, train, test]) == original
    train["labels"]["noise"] = True
    assert development_digest([train, val, test]) != original


def test_manifest_duplicate_and_invalid_label_rejected(tmp_path):
    path = tmp_path / "manifest.jsonl"
    write_manifest(path, [row(), row()])
    with pytest.raises(ValueError, match="unique"):
        read_manifest(path)
    invalid = row()
    invalid["labels"]["noise"] = "False"
    write_manifest(path, [invalid])
    with pytest.raises(ValueError, match="Invalid labels"):
        read_manifest(path)


@pytest.mark.parametrize("defect", DEFECTS)
def test_seeded_transforms_preserve_valid_images(product, defect):
    mask = np.zeros(product.shape[:2], bool)
    mask[12:68, 15:45] = True
    first, first_mask, parameters = transform(product, defect, 2, 42, mask)
    second, _, _ = transform(product, defect, 2, 42, mask)
    assert np.array_equal(first, second)
    assert first.dtype == np.uint8
    assert first_mask.shape == first.shape[:2]
    if defect == "low_resolution":
        assert min(first.shape[:2]) < 60
    if defect == "possible_cropping":
        assert parameters["retained_product_fraction"] >= 0.75
        assert first_mask[:, 0].any()


def test_background_transform_requires_human_mask(product):
    with pytest.raises(ValueError, match="reviewed"):
        transform(product, "background_uniformity", 1, 42)


def test_changed_image_is_rejected(tmp_path):
    import hashlib
    image = tmp_path / "image.png"
    image.write_bytes(b"original bytes")
    item = {"id": "product", "image": str(image), "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest()}
    assert image_bytes(item) == b"original bytes"
    image.write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="changed after manifest"):
        image_bytes(item)


def test_manifest_context_is_validated_but_legacy_rows_are_readable(tmp_path):
    path = tmp_path / "manifest.jsonl"
    legacy = row()
    write_manifest(path, [legacy])
    assert "photo_context" not in read_manifest(path)[0]
    legacy["photo_context"] = "unverified_guess"
    write_manifest(path, [legacy])
    with pytest.raises(ValueError, match="Photo context"):
        read_manifest(path)
