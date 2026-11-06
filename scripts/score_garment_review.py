"""Score existing pilot predictions only after every reference has a saved human approval."""
import argparse
import csv
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.garment_pilot import digest, mask_metrics, timing
from scripts.garment_review import DEFAULT_ROOT, current


def binary_mask(path, size):
    with Image.open(path) as image:
        if image.format != "PNG" or list(image.size) != size:
            raise ValueError(f"Mask is not a native-size PNG: {path}")
        pixels = np.asarray(image.convert("RGBA"))
    gray = pixels[:, :, 0]
    if not (np.isin(gray, [0, 255]).all() and (pixels[:, :, 1] == gray).all()
            and (pixels[:, :, 2] == gray).all() and (pixels[:, :, 3] == 255).all()):
        raise ValueError(f"Mask is not opaque binary black/white: {path}")
    return gray == 255


def verified_inputs(root):
    """Complete the approval/integrity gate before calculating any accuracy metric."""
    root = Path(root)
    catalog = json.loads((root / "review.json").read_text(encoding="utf-8"))
    results = json.loads((root / "predictions/results.json").read_text(encoding="utf-8"))
    rows = {row["id"]: row for row in results["records"]}
    ids = [case["id"] for case in catalog["cases"]]
    if not ids or len(set(ids)) != len(ids) or set(ids) != set(rows) or len(rows) != len(results["records"]):
        raise ValueError("Review cases must match the prediction records exactly.")
    verified = []
    for case in catalog["cases"]:
        row = rows[case["id"]]
        approval, reference_path = current(root, case)
        if (approval.get("approved") is not True or approval.get("confirmed_visible_fabric") is not True
                or not approval.get("reviewer", "").strip() or approval.get("revision", 0) < 1):
            raise ValueError(f"Saved human approval missing: {case['id']}")
        if (digest(case["image"]) != case["image_sha256"] or row["image_sha256"] != case["image_sha256"]
                or approval.get("image_sha256") != case["image_sha256"]
                or approval.get("native_size") != case["native_size"] or row["native_size"] != case["native_size"]
                or approval.get("target") != case["target"] or row["target"] != case["target"]):
            raise ValueError(f"Source, target or approval dimensions changed: {case['id']}")
        with Image.open(case["image"]) as image:
            if list(image.size) != case["native_size"]:
                raise ValueError(f"Source dimensions changed: {case['id']}")
        reference = binary_mask(reference_path, case["native_size"])
        if not reference.any() or reference.all():
            raise ValueError(f"Empty or full-frame shirt reference: {case['id']}")
        proposal_path = root / "references" / case["id"] / "proposal.png"
        if digest(proposal_path) != case["proposal_sha256"]:
            raise ValueError(f"Initial proposal changed: {case['id']}")
        proposal = binary_mask(proposal_path, case["native_size"])
        predictions = {}
        for method in ("baseline", "pretrained"):
            path = root / "predictions" / case["id"] / (method + "-mask.png")
            if digest(path) != case["prediction_sha256"][method]:
                raise ValueError(f"Prediction changed: {case['id']} / {method}")
            predictions[method] = binary_mask(path, case["native_size"])
        verified.append((case, row, approval, reference_path, reference, proposal, predictions))
    return verified, results


def score(root, output):
    root, output = Path(root), Path(output)
    verified, previous = verified_inputs(root)
    if output.exists():
        raise ValueError("Evaluation output already exists; preserve prior evidence.")
    output.mkdir(parents=True)
    records, table, cards = [], [], []
    for case, row, approval, reference_path, reference, proposal, predictions in verified:
        folder = output / case["id"]
        folder.mkdir()
        (folder / "reference.png").write_bytes(reference_path.read_bytes())
        (folder / "approval.json").write_text(json.dumps(approval, indent=2), encoding="utf-8")
        draft = {"baseline": "DIP", "pretrained": "SegFormer", "blank": "Blank"}[case["seed"]]
        record = {"id": case["id"], "context": row["context"], "target": case["target"],
                  "image_sha256": case["image_sha256"], "native_size": case["native_size"],
                  "reference": {"status": "human-approved", "approval": approval,
                                "approval_file_sha256": digest(reference_path.parent / "review.json"),
                                "mask_path": str(reference_path), "initial_draft": draft,
                                "changed_pixels_from_initial_draft": int(np.count_nonzero(reference != proposal)),
                                "independent_blind_reference": False}, "methods": {}}
        with Image.open(root / "predictions" / case["id"] / "original.png") as image:
            original = np.array(image.convert("RGB"))
        for method, predicted in predictions.items():
            intersection = int(np.count_nonzero(reference & predicted))
            false_negative = reference & ~predicted
            false_positive = predicted & ~reference
            stats = {**mask_metrics(predicted, reference), "true_positive_pixels": intersection,
                     "missed_fabric_pixels": int(false_negative.sum()), "extra_region_pixels": int(false_positive.sum()),
                     "reference_pixels": int(reference.sum()), "predicted_pixels": int(predicted.sum()),
                     "runtime": timing(row[method + "_ms"]),
                     "prediction_sha256": case["prediction_sha256"][method],
                     "wrong_item_selection": None, "visual_review_status": "pending qualitative review"}
            record["methods"][method] = stats
            table.append({"id": case["id"], "method": method, "reference_status": "human-approved",
                          "initial_draft": draft, **{key: stats[key] for key in
                          ("iou", "missed_reference_fraction", "outside_reference_fraction", "missed_fabric_pixels", "extra_region_pixels")},
                          "median_ms": stats["runtime"]["median_ms"], "p95_ms": stats["runtime"]["p95_ms"]})
            colors = original.copy()
            for region, color in ((reference & predicted, [0, 200, 140]),
                                  (false_negative, [30, 110, 255]), (false_positive, [255, 40, 40])):
                colors[region] = (.45 * colors[region] + .55 * np.array(color)).astype(np.uint8)
            Image.fromarray(colors).save(folder / (method + "-errors.png"))
        records.append(record)
        assets = [(Path(os.path.relpath(root / "predictions" / case["id"] / "original.png", output)).as_posix(), "Original"),
                  (f"{case['id']}/reference.png", f"Human-approved reference; initial: {draft}"),
                  (f"{case['id']}/baseline-errors.png", "DIP errors"),
                  (f"{case['id']}/pretrained-errors.png", "SegFormer errors")]
        figures = ''.join(f'<figure><a href="{html.escape(path)}"><img src="{html.escape(path)}" alt="{html.escape(label)}"></a><figcaption>{html.escape(label)}</figcaption></figure>' for path, label in assets)
        cards.append(f'<article><h2>{html.escape(case["id"])}</h2><p>{html.escape(case["target"])}</p><div>{figures}</div></article>')
    result = {"status": "reference_metrics_complete_qualitative_review_pending", "created_at": datetime.now(timezone.utc).isoformat(),
              "photos": len(records), "reference_status": "human-approved; draft provenance is recorded per photo",
              "provenance_warning": "Predictions were visible during reference review. These are not blind independent annotations. Exact unchanged model drafts can inflate that model's agreement.",
              "prediction_results_sha256": digest(root / "predictions/results.json"),
              "review_catalog_sha256": digest(root / "review.json"), "model": previous["model"],
              "baseline_timing": previous["baseline_timing"], "pretrained_timing": previous["pretrained_timing"],
              "timing_policy": previous["timing_policy"], "predictions_rerun": False,
              "metric_definitions": {"iou": "TP / (TP + FP + FN)", "missed_reference_fraction": "FN / reference pixels",
                                     "outside_reference_fraction": "FP / predicted pixels; null for an empty prediction"},
              "DIP_quality_verdicts_changed": False, "records": records}
    (output / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    with (output / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Approved shirt reference comparison</title><style>body{font:16px system-ui;margin:24px}article{border-top:1px solid #ccc}article div{display:flex;gap:12px;flex-wrap:wrap}figure{width:23%;min-width:210px;margin:0}img{width:100%;height:340px;object-fit:contain}</style><h1>Human-approved reference comparison</h1><p>Green: matched fabric. Blue: missed fabric. Red: extra prediction. Uncoloured: matched background. Open images for native pixels. References were reviewed with predictions visible; initial drafts are identified.</p>' + ''.join(cards) + '</html>'
    (output / "comparison.html").write_text(document, encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = score(args.root, args.output)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Scoring blocked: {error}\n")
    print(json.dumps({"status": result["status"], "photos": result["photos"]}))


if __name__ == "__main__":
    main()
