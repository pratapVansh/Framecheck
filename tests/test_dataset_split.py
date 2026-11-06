"""Split guarantees use procedural fixtures, never performance measurements."""
from copy import deepcopy
import hashlib

import numpy as np
import pytest

from inspector.benchmark import read_manifest, rules_hash, verify_split_freeze, write_manifest
from inspector.images import png_bytes
from inspector.models import DEFECTS, InspectionProfile
from scripts.dataset import split_manifest


@pytest.fixture
def split_rows(tmp_path):
    mask = np.zeros((80, 60), np.uint8)
    mask[12:68, 15:45] = 255
    mask_path = tmp_path / "mask.png"
    mask_data = png_bytes(mask)
    mask_path.write_bytes(mask_data)
    rows = []
    for category in ("apparel", "footwear", "bags", "watches"):
        for position in range(75):
            index = len(rows)
            rgb = np.full((80, 60, 3), 255, np.uint8)
            rgb[12:68, 15:45] = 100
            rgb[20, 20] = (index % 256, index // 256, 0)
            data = png_bytes(rgb)
            image = tmp_path / f"{index}.png"
            image.write_bytes(data)
            rows.append({
                "id": str(index), "source_id": str(index), "group_id": str(index),
                "image": str(image), "image_sha256": hashlib.sha256(data).hexdigest(),
                "source_hash": hashlib.sha256(rgb.tobytes() + str(rgb.shape).encode()).hexdigest(),
                "category": category, "kind": "original", "split": None,
                "labels": dict.fromkeys(DEFECTS, False), "reviewed": True,
                "review_method": "ai_visual_review", "human_verified": False,
                "rules_hash": rules_hash(InspectionProfile()),
                "mask": str(mask_path) if position < 15 else None,
                "mask_reviewed": position < 15,
                "mask_sha256": hashlib.sha256(mask_data).hexdigest() if position < 15 else None,
            })
    return rows


def test_mask_balanced_splits_are_group_safe_frozen_and_order_independent(tmp_path, split_rows):
    extra = deepcopy(split_rows[0])
    extra.update(id="alternate", source_id="alternate")
    split_rows.append(extra)
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    write_manifest(first, split_rows)
    write_manifest(second, list(reversed(split_rows)))
    before = first.read_bytes()
    report = split_manifest(first, 42)
    split_manifest(second, 42)
    assigned = read_manifest(first)
    assert {r["id"]: r["split"] for r in assigned} == {r["id"]: r["split"] for r in read_manifest(second)}
    assert [report["split_counts"][s]["groups"] for s in ("train", "validation", "test")] == [180, 60, 60]
    for counts in report["split_counts"].values():
        assert counts["reviewed_mask_groups"] == 20
        assert counts["mask_categories"] == dict.fromkeys(("apparel", "footwear", "bags", "watches"), 5)
    assert next(r["split"] for r in assigned if r["id"] == "0") == next(r["split"] for r in assigned if r["id"] == "alternate")
    assert first.with_suffix(".before-split.jsonl.bak").read_bytes() == before
    assert first.with_suffix(".split-freeze.jsonl.bak").read_bytes() == first.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == report["frozen_sha256"]
    assert report["human_verified_images"] == 0
    assert all(r["review_method"] == "ai_visual_review" and r["human_verified"] is False for r in assigned)
    frozen = first.read_bytes()
    with pytest.raises(ValueError, match="already assigned"):
        split_manifest(first, 42)
    assert first.read_bytes() == frozen


@pytest.mark.parametrize("defect", ["insufficient_masks", "source_hash", "mask_hash", "related_source"])
def test_invalid_split_inputs_preserve_manifest(tmp_path, split_rows, defect):
    if defect == "insufficient_masks":
        split_rows[0]["mask_reviewed"] = False
    elif defect == "source_hash":
        split_rows[0]["source_hash"] = "changed"
    elif defect == "mask_hash":
        split_rows[0]["mask_sha256"] = "changed"
    else:
        split_rows[1]["source_id"] = split_rows[0]["source_id"]
    manifest = tmp_path / "invalid.jsonl"
    write_manifest(manifest, split_rows)
    before = manifest.read_bytes()
    with pytest.raises(ValueError):
        split_manifest(manifest, 42)
    assert manifest.read_bytes() == before
    assert not manifest.with_suffix(".before-split.jsonl.bak").exists()
    assert not manifest.with_suffix(".split-freeze.jsonl.bak").exists()
    assert not manifest.with_suffix(".split.json").exists()


@pytest.mark.parametrize("changed", ["manifest", "snapshot", "mask", "missing_report"])
def test_generation_rejects_changes_to_frozen_sources(tmp_path, split_rows, changed):
    from scripts.generate import generate
    manifest = tmp_path / "frozen.jsonl"
    write_manifest(manifest, split_rows)
    split_manifest(manifest, 42)
    verify_split_freeze(manifest)
    if changed == "manifest":
        rows = read_manifest(manifest)
        rows[0]["labels"]["blur"] = True
        write_manifest(manifest, rows)
    elif changed == "snapshot":
        with manifest.with_suffix(".split-freeze.jsonl.bak").open("ab") as handle:
            handle.write(b"\n")
    elif changed == "mask":
        mask = np.zeros((80, 60), np.uint8)
        mask[5:70, 10:50] = 255
        from pathlib import Path
        Path(split_rows[0]["mask"]).write_bytes(png_bytes(mask))
    else:
        manifest.with_suffix(".split.json").unlink()
    output = tmp_path / "benchmark.jsonl"
    with pytest.raises(ValueError, match="freeze|Frozen"):
        generate(manifest, output, per_split=1)
    assert not output.exists() and not (tmp_path / "benchmark_variants").exists()


def test_split_artifact_failure_rolls_back_manifest(tmp_path, split_rows, monkeypatch):
    from pathlib import Path
    manifest = tmp_path / "failure.jsonl"
    write_manifest(manifest, split_rows)
    before = manifest.read_bytes()
    original_open = Path.open

    def failing_open(path, *args, **kwargs):
        if path == manifest.with_suffix(".split.json") and args and args[0] == "xb":
            raise OSError("simulated split-report failure")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError, match="simulated"):
        split_manifest(manifest, 42)
    assert manifest.read_bytes() == before
    assert not manifest.with_suffix(".before-split.jsonl.bak").exists()
    assert not manifest.with_suffix(".split-freeze.jsonl.bak").exists()
