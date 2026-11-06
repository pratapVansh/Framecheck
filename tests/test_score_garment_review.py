import base64
from io import BytesIO
import json

import numpy as np
from PIL import Image
import pytest

from scripts.garment_pilot import digest
from scripts.garment_review import save_review
from scripts.score_garment_review import score, verified_inputs


@pytest.fixture
def scoring_inputs(tmp_path):
    cases, rows = [], []
    reference = np.zeros((12, 16), np.uint8)
    reference[2:10, 4:12] = 255
    partial = reference.copy()
    partial[6:] = 0
    for index in range(2):
        case_id = f"synthetic-{index}"
        folder = tmp_path / "predictions" / case_id
        folder.mkdir(parents=True)
        Image.new("RGB", (16, 12), "gray").save(folder / "original.png")
        Image.fromarray(reference).save(folder / "baseline-mask.png")
        Image.fromarray(partial).save(folder / "pretrained-mask.png")
        reference_folder = tmp_path / "references" / case_id
        reference_folder.mkdir(parents=True)
        Image.fromarray(reference).save(reference_folder / "proposal.png")
        case = {"id": case_id, "image": str(folder / "original.png"),
                "image_sha256": digest(folder / "original.png"), "native_size": [16, 12],
                "target": "Synthetic rectangle, not a real garment reference", "seed": "baseline",
                "proposal_sha256": digest(reference_folder / "proposal.png"),
                "prediction_sha256": {method: digest(folder / (method + "-mask.png"))
                                      for method in ("baseline", "pretrained")}}
        cases.append(case)
        rows.append({**case, "context": "isolated_product", "baseline_ms": [1, 2, 3], "pretrained_ms": [10, 20, 30]})
    (tmp_path / "review.json").write_text(json.dumps({"cases": cases}), encoding="utf-8")
    (tmp_path / "predictions/results.json").write_text(json.dumps({"records": rows, "model": {"device": "cpu"},
        "baseline_timing": {}, "pretrained_timing": {}, "timing_policy": "Synthetic test only"}), encoding="utf-8")
    stream = BytesIO()
    Image.fromarray(reference).save(stream, format="PNG")
    payload = {"revision": 0, "approved": True, "confirmed": True, "reviewer": "Synthetic fixture",
               "notes": "Test data only", "seed": "baseline",
               "mask": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()}
    return tmp_path, cases, payload


def test_all_approvals_required_before_any_metric_or_output(scoring_inputs, monkeypatch):
    root, cases, payload = scoring_inputs
    save_review(root, cases[0], payload)
    def forbidden_metric(*args):
        pytest.fail("Accuracy computed before all approvals were validated")
    monkeypatch.setattr("scripts.score_garment_review.mask_metrics", forbidden_metric)
    with pytest.raises(ValueError, match="Saved human approval missing: synthetic-1"):
        score(root, root / "evaluation")
    assert not (root / "evaluation").exists()


def test_existing_predictions_scores_and_reference_provenance(scoring_inputs):
    root, cases, payload = scoring_inputs
    for case in cases:
        save_review(root, case, payload)
    result = score(root, root / "evaluation")
    assert not result["predictions_rerun"] and not result["DIP_quality_verdicts_changed"]
    for row in result["records"]:
        assert row["reference"]["status"] == "human-approved"
        assert row["reference"]["initial_draft"] == "DIP"
        assert row["reference"]["changed_pixels_from_initial_draft"] == 0
        assert not row["reference"]["independent_blind_reference"]
        assert row["methods"]["baseline"]["iou"] == 1
        assert row["methods"]["pretrained"]["iou"] == .5
        assert row["methods"]["pretrained"]["missed_reference_fraction"] == .5
        assert row["methods"]["pretrained"]["extra_region_pixels"] == 0
    assert (root / "evaluation/metrics.csv").is_file()
    with pytest.raises(ValueError, match="already exists"):
        score(root, root / "evaluation")


def test_approved_reference_does_not_excuse_changed_predictions_or_sources(scoring_inputs):
    root, cases, payload = scoring_inputs
    for case in cases:
        save_review(root, case, payload)
    prediction = root / "predictions/synthetic-0/pretrained-mask.png"
    original = prediction.read_bytes()
    Image.new("L", (16, 12), 0).save(prediction)
    with pytest.raises(ValueError, match="Prediction changed"):
        verified_inputs(root)
    prediction.write_bytes(original)
    Image.new("RGB", (16, 12), "red").save(cases[0]["image"])
    with pytest.raises(ValueError, match="Source, target or approval"):
        verified_inputs(root)
