import json

import numpy as np
import pytest

from inspector.images import png_bytes
from scripts.public_shirts import prepare, validate_original


def test_original_resolution_check_rejects_a_preview_instead_of_upscaling():
    data = png_bytes(np.full((12, 20, 3), 127, np.uint8))
    assert validate_original(data, [20, 12]).shape == (12, 20, 3)
    with pytest.raises(ValueError, match="reject preview"):
        validate_original(data, [200, 120])


def test_missing_originals_are_queued_without_fake_images_or_reference_masks(tmp_path):
    catalog = tmp_path / "sources.json"
    source = {"id": "missing", "title": "Missing original", "source_url": "https://example.org/photo", "original_url": "https://example.org/original.jpg",
              "author": "Photographer", "license": "CC0-1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
              "reuse_conditions": "CC0", "expected_size": [20, 12], "planned_context": "uncertain", "target": "shirt",
              "case": "test source", "local_file": str(tmp_path / "not-downloaded.jpg")}
    catalog.write_text(json.dumps([source]))
    output = tmp_path / "queue"
    assert prepare(catalog, output) == []
    queue = json.loads((output / "human-review-queue.jsonl").read_text())
    assert not queue["source_available"] and not queue["human_verified"] and queue["reference_mask"] is None
    assert (output / "manifest.jsonl").read_text() == ""
    assert not (tmp_path / "not-downloaded.jpg").exists()


def test_a_source_without_license_evidence_cannot_enter_the_manifest(tmp_path):
    catalog = tmp_path / "sources.json"
    catalog.write_text(json.dumps([{"id": "unlicensed"}]))
    with pytest.raises(ValueError, match="license record"):
        prepare(catalog, tmp_path / "output")
