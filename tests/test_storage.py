from dataclasses import replace
import sqlite3

import pytest

from inspector.pipeline import inspect_image
from inspector.storage import list_inspections, load_inspection, save_inspection


def test_roundtrip_and_duplicates(tmp_path, product_bytes):
    path = tmp_path / "inspections.sqlite3"
    result = inspect_image(product_bytes)
    save_inspection(result, "product'; DROP TABLE inspections;--.png", path)
    reopened = load_inspection(result.inspection_id, path)
    assert reopened["result_json"] == result.to_json()
    assert reopened["mask_png"] == result.mask_png
    assert len(list_inspections(path)) == 1
    with pytest.raises(sqlite3.IntegrityError):
        save_inspection(result, "duplicate.png", path)
    assert len(list_inspections(path)) == 1
    assert load_inspection("missing", path) is None


def test_check_failure_rolls_back_parent(tmp_path, product_bytes):
    path = tmp_path / "inspections.sqlite3"
    result = inspect_image(product_bytes)
    result.checks.append(result.checks[0])
    with pytest.raises(sqlite3.IntegrityError):
        save_inspection(result, "product.png", path)
    assert list_inspections(path) == []
