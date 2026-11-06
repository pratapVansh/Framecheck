"""Toolchain integration uses procedural test fixtures, never dataset performance claims."""
from copy import deepcopy
import csv
import hashlib
import json

import numpy as np
import pytest

from inspector.benchmark import read_manifest, rules_hash, write_manifest
from inspector.images import png_bytes
from inspector.models import DEFECTS, InspectionProfile
from scripts.calibrate import calibrate
from scripts.dataset import audit, split_manifest
from scripts.evaluate import evaluate
from scripts.generate import generate


def test_audit_review_split_generate(tmp_path, product):
    raw = tmp_path / "raw"
    images = raw / "images"
    images.mkdir(parents=True)
    metadata = raw / "styles.csv"
    with metadata.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "masterCategory", "subCategory", "articleType"])
        for index in range(20):
            category = index // 5
            details = [("Apparel", "Topwear", "Shirts"), ("Footwear", "Shoes", "Casual Shoes"), ("Accessories", "Bags", "Handbags"), ("Accessories", "Watches", "Watches")][category]
            writer.writerow([str(index), *details])
            changed = product.copy()
            changed[12:68, 15:45, 0] = product[12:68, 15:45, 0] + index * 3
            from PIL import Image
            Image.fromarray(changed).save(images / f"{index}.jpg")
        writer.writerow(["malformed", "only two cells"])
        writer.writerow(["missing", "Apparel", "Topwear", "Shirts"])
    manifest = tmp_path / "manifest.jsonl"
    audit(raw, manifest, 20, 42)
    rows = read_manifest(manifest)
    assert len(rows) == 20
    assert {row["category"] for row in rows} == {"apparel", "footwear", "bags", "watches"}
    assert not any(row["reviewed"] for row in rows)
    with pytest.raises(ValueError, match="Review every"):
        split_manifest(manifest, 42)
    mask = np.zeros((80, 60), np.uint8)
    mask[12:68, 15:45] = 255
    mask_path = tmp_path / "fixture-mask.png"
    mask_path.write_bytes(png_bytes(mask))
    for row in rows:
        row["labels"] = dict.fromkeys(DEFECTS, False)
        row["reviewed"] = True
        row["reviewer"] = "procedural test fixture"
        row["photo_context"] = "isolated_product"
        row.update(review_method="ai_visual_review", human_verified=False, human_confirmed=False,
                   ai_review={"historical_source_review": True}, mask=str(mask_path), mask_reviewed=True,
                   mask_sha256=hashlib.sha256(mask_path.read_bytes()).hexdigest(),
                   mask_review_method="ai_visual_review", mask_human_verified=False)
    write_manifest(manifest, rows)
    split_manifest(manifest, 42, min_mask_groups=0)
    rows = read_manifest(manifest)
    assert {split: sum(row["split"] == split for row in rows) for split in ("train", "validation", "test")} == {"train": 12, "validation": 4, "test": 4}
    with pytest.raises(ValueError, match="already assigned"):
        split_manifest(manifest, 42)
    generated = tmp_path / "benchmark.jsonl"
    generate(manifest, generated, per_split=1)
    variants = read_manifest(generated)
    sources = {row["source_id"]: row for row in rows}
    assert len(variants) > len(rows)
    for row in variants:
        assert row["split"] == sources[row["source_id"]]["split"]
        assert row["photo_context"] == sources[row["source_id"]]["photo_context"]
        if row["kind"] != "original":
            assert not row["reviewed"]
            assert "review_method" not in row and "ai_review" not in row
            assert not row["human_verified"] and not row["human_confirmed"]
            assert row["source_annotation_provenance"]["review_method"] == "ai_visual_review"
            if row.get("mask"):
                from pathlib import Path
                assert not row["mask_reviewed"] and "mask_review_method" not in row
                assert not row["mask_human_verified"]
                assert row["mask_sha256"] == hashlib.sha256(Path(row["mask"]).read_bytes()).hexdigest()


def test_calibrate_freeze_evaluate_and_tampering(tmp_path, product_bytes, monkeypatch):
    image = tmp_path / "fixture.png"
    image.write_bytes(product_bytes)
    rows = []
    # Deliberately interleave development splits to exercise canonical provenance hashing.
    for split in ("validation", "train", "test", "train", "validation", "test"):
        index = len(rows)
        rows.append({"id": str(index), "source_id": str(index), "group_id": str(index), "image": str(image),
                     "split": split, "labels": dict.fromkeys(DEFECTS, False), "reviewed": True,
                     "photo_context": "model_worn" if index == 2 else "isolated_product",
                     "rules_hash": rules_hash(InspectionProfile()), "kind": "original", "category": "test_fixture"})
    manifest = tmp_path / "benchmark.jsonl"
    write_manifest(manifest, rows)
    profile = tmp_path / "calibrated-profile.json"
    measured_splits = []
    def development_image_bytes(row):
        assert row["split"] in {"train", "validation"}
        measured_splits.append(row["split"])
        return product_bytes
    with monkeypatch.context() as patch:
        patch.setattr("scripts.calibrate.image_bytes", development_image_bytes)
        calibrate(manifest, profile)
    assert measured_splits == ["train", "train", "validation", "validation"]
    frozen = json.loads(profile.read_text())
    assert frozen["calibrated"]
    calibration = json.loads(profile.with_suffix(".calibration.json").read_text())
    assert calibration["test_images_used"] == 0
    assert len(calibration["search"]) == 16
    assert all(step["candidates"] for step in calibration["search"])
    assert calibration["search"][-1]["field"] == "review_band"
    assert calibration["calibration_dimensions"]["calibration_short_min"] == 60
    assert calibration["annotation_provenance"]["train"]["human_verified_images"] == 0
    output = tmp_path / "evaluation.json"
    evaluate(manifest, profile, output)
    report = json.loads(output.read_text())
    assert report["metrics"]["images"] == 2
    assert report["metrics"]["false_reject_rate"] == 0
    assert report["metrics"]["defect_recall"] is None
    assert report["metrics"]["manual_review_rate"] == 0.5
    assert report["denominators"]["all_test_images"] == 2
    assert report["denominators"]["unknown_label_instances"] == 0
    assert report["annotation_provenance"]["human_verified_images"] == 0
    assert report["manual_review_by_check"]["blur"] == {"count": 1, "denominator": 2}
    assert [p["photo_context"] for p in report["predictions"]] == ["model_worn", "isolated_product"]
    assert report["metrics"]["advisory_cropping"]["true_cases"] == 0
    assert not report["targets"]["ten_positive_product_groups_per_category"]
    assert output.with_suffix(".html").exists()
    with pytest.raises(ValueError, match="already exists|output exists"):
        evaluate(manifest, profile, output)
    frozen["min_sharpness"] += 1
    profile.write_text(json.dumps(frozen))
    with pytest.raises(ValueError, match="changed after calibration"):
        evaluate(manifest, profile, tmp_path / "tampered.json")


def test_legacy_context_cannot_be_used_to_fit_quality_thresholds(tmp_path, product_bytes):
    image = tmp_path / "fixture.png"
    image.write_bytes(product_bytes)
    rows = [{"id": split, "source_id": split, "group_id": split, "image": str(image), "split": split,
             "labels": dict.fromkeys(DEFECTS, False), "reviewed": True, "kind": "original",
             "rules_hash": rules_hash(InspectionProfile())} for split in ("train", "validation")]
    manifest = tmp_path / "legacy.jsonl"
    write_manifest(manifest, rows)
    profile = tmp_path / "rejected-profile.json"
    with pytest.raises(ValueError, match="confirmed photo context"):
        calibrate(manifest, profile)
    assert not profile.exists()
