"""Evaluate a frozen profile on untouched, reviewed test product groups."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path

import numpy as np
from PIL import Image

from inspector import PIPELINE_VERSION
from inspector.benchmark import development_digest, image_bytes, read_manifest, report_slices, rules_hash, summarize_predictions
from inspector.images import decode_image
from inspector.models import InspectionProfile, PhotoContext, Status
from inspector.pipeline import inspect_image


def evaluate(manifest, profile_path, output):
    if output.exists() or output.with_suffix(".html").exists():
        raise ValueError("Evaluation output exists. Preserve it and use a new report name.")
    profile = InspectionProfile.from_dict(json.loads(profile_path.read_text(encoding="utf-8")))
    if not profile.calibrated:
        raise ValueError("Freeze a train/validation-calibrated profile before test evaluation.")
    calibration_path = profile_path.with_suffix(".calibration.json")
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    if calibration["profile_sha256"] != hashlib.sha256(profile_path.read_bytes()).hexdigest() or calibration["pipeline_version"] != PIPELINE_VERSION:
        raise ValueError("Profile or pipeline changed after calibration. Create a new calibration version.")
    all_rows = read_manifest(manifest)
    development = [row for row in all_rows if row["split"] in {"train", "validation"}]
    digest = development_digest(development)
    if digest != calibration["development_sha256"]:
        raise ValueError("Development manifest changed after calibration; use the frozen manifest or recalibrate.")
    rows = [row for row in all_rows if row["split"] == "test"]
    if not rows or not all(row["reviewed"] for row in rows):
        raise ValueError("Every test row must be independently reviewed before evaluation.")
    if any(row["rules_hash"] != rules_hash(profile) for row in rows):
        raise ValueError("Test ground truth was labelled against different requirement rules.")
    development_groups = set(calibration["training_groups"] + calibration["validation_groups"])
    if any(row["group_id"] in development_groups for row in rows):
        raise ValueError("A test product group was used during development.")
    predictions, results, mask_ious, mask_exclusions = [], [], [], []
    for row in rows:
        context = PhotoContext.parse(row.get("photo_context", PhotoContext.UNCERTAIN))
        result = inspect_image(image_bytes(row), profile, photo_context=context)
        predictions.append({"id": row["id"], "group_id": row["group_id"], "status": result.status,
                            "photo_context": context,
                            "checks": {check.identifier: check.status for check in result.checks}, "duration_ms": result.duration_ms})
        results.append(result)
        if row.get("mask") and row.get("mask_reviewed"):
            if context != PhotoContext.ISOLATED_PRODUCT:
                mask_exclusions.append({"id": row["id"], "reason": "Product-only photo context is not confirmed; foreground is not a product-mask prediction."})
                continue
            with Image.open(row["mask"]) as image:
                truth = np.array(image.convert("L")) > 127
            mask_rgb, _ = decode_image(result.mask_png)
            predicted = mask_rgb[:, :, 0] > 127
            if truth.shape != predicted.shape:
                raise ValueError(f"Reference mask dimensions differ for {row['id']}.")
            union = np.count_nonzero(truth | predicted)
            mask_ious.append({"id": row["id"], "iou": float(np.count_nonzero(truth & predicted) / union) if union else 1.0})
    metrics = summarize_predictions(rows, predictions)
    durations = [pred["duration_ms"] for pred in predictions]
    acceptable_rate = metrics["false_reject_rate"]
    recall = metrics["defect_recall"]
    targets = {
        "macro_f1_at_least_0.75": metrics["macro_f1"] >= 0.75,
        "defect_recall_at_least_0.80": recall is not None and recall >= 0.80,
        "false_reject_rate_at_most_0.10": acceptable_rate is not None and acceptable_rate <= 0.10,
        "manual_review_rate_at_most_0.30": metrics["manual_review_rate"] <= 0.30,
        "ten_positive_product_groups_per_category": metrics["complete_category_support"],
    }
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "pipeline_version": PIPELINE_VERSION,
        "calibration_id": profile.calibration_id, "profile": profile.to_dict(),
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "metrics": metrics, "slices": report_slices(rows, predictions), "targets": targets,
        "annotation_provenance": {
            "image_review_methods": dict(Counter(row.get("review_method", "unrecorded") for row in rows)),
            "human_verified_images": sum(bool(row.get("human_verified")) for row in rows),
            "mask_review_methods": dict(Counter(row.get("mask_review_method", "unrecorded") for row in rows if row.get("mask_reviewed"))),
            "human_verified_masks": sum(bool(row.get("mask_human_verified")) for row in rows),
        },
        "denominators": {"all_test_images": len(rows), "fully_labelled_acceptable_images": metrics["acceptable_images"],
                         "images_with_confirmed_defects": sum(any(v is True for v in row["labels"].values()) for row in rows),
                         "known_positive_defect_instances": metrics["actual_defect_instances"],
                         "unknown_label_instances": sum(v is None for row in rows for v in row["labels"].values())},
        "manual_review_by_check": {key: {"count": sum(pred["checks"][key] == Status.REVIEW for pred in predictions),
                                         "denominator": len(rows)} for key in rows[0]["labels"]},
        "latency_ms": {"median": float(np.median(durations)), "p95": float(np.percentile(durations, 95)), "includes": "decode, inspection and evidence encoding; excludes SQLite and UI"},
        "mask_iou": {"count": len(mask_ious), "mean": float(np.mean([value["iou"] for value in mask_ious])) if mask_ious else None, "per_image": mask_ious, "excluded": mask_exclusions},
        "predictions": predictions,
        "limitations": [
            "Synthetic variants are correlated within source products and may reveal transformation artifacts; they do not establish real-world marketplace performance.",
            "Low-resolution images limit fine detail, noise attribution, segmentation and cropping evidence. Natural product color can resemble exposure defects.",
            "Labels and approximate reference masks use recorded AI review, not independent human verification. Missing category support or acceptable examples prevents a complete evaluation claim.",
            "Only definitive Fail checks count as primary detections. Advisory possible-cropping reviews do not count as definitive positives; their capture counts are reported separately.",
            "Product-dependent decisions are automatic only for confirmed isolated-product context. Model-worn, uncertain and legacy missing-context records require review. Cropping primary F1/recall are zero on positive truth when the advisory check never emits Fail.",
            "Do not tune on this test report. A changed method needs a new untouched evaluation set before claiming independent performance.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    assets = output.parent / (output.stem + "_evidence")
    if assets.exists():
        raise ValueError("Evidence directory already exists. Choose a new report name.")
    assets.mkdir()
    examples = []
    example_counts = Counter()
    for row, prediction, result in zip(rows, predictions, results):
        false_reject = all(value is False for value in row["labels"].values()) and result.status == Status.FAIL
        missed = any(value is True and prediction["checks"][key] != Status.FAIL for key, value in row["labels"].items())
        reason = "False reject" if false_reject else "Missed or unresolved defect" if missed else "Manual review" if result.status == Status.REVIEW else ""
        if reason and example_counts[reason] < 5:
            example_counts[reason] += 1
            filename = hashlib.sha256(row["id"].encode()).hexdigest()[:16] + ".png"
            (assets / filename).write_bytes(result.overlay_png)
            examples.append(f'<figure><img alt="Inspection evidence" src="{html.escape(assets.name)}/{filename}"><figcaption>{html.escape(reason)}: {html.escape(row["id"])}<br>{html.escape("; ".join(check.explanation for check in result.checks if check.status != Status.PASS))}</figcaption></figure>')
    rows_html = "".join(f'<tr><td>{html.escape(key)}</td><td>{value["precision"]:.3f}</td><td>{value["recall"]:.3f}</td><td>{value["f1"]:.3f}</td><td>{value["positive_support"]}</td><td>{value["positive_product_groups"]}</td></tr>' for key, value in metrics["per_category"].items())
    report_html = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Image inspector evaluation</title><style>body{font:16px/1.5 system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#173044}td,th{padding:8px;border-bottom:1px solid #ccd}table{border-collapse:collapse;width:100%}figure{display:inline-block;vertical-align:top;width:300px;margin:15px}img{width:100%;height:250px;object-fit:contain}figcaption{font-size:12px}pre{white-space:pre-wrap}</style><h1>Held-out inspection evaluation</h1><p>Test data never selected thresholds. Missing support and unmet targets remain visible.</p>'
    report_html += '<pre>' + html.escape(json.dumps({key: value for key, value in metrics.items() if key != "per_category"}, indent=2)) + '</pre><h2>Per-category metrics</h2><table><tr><th>Defect</th><th>Precision</th><th>Recall</th><th>F1</th><th>Positive support</th><th>Product groups</th></tr>' + rows_html + '</table><h2>Targets</h2><pre>' + html.escape(json.dumps(targets, indent=2)) + '</pre><h2>Provenance, latency, and masks</h2><pre>' + html.escape(json.dumps({key: report[key] for key in ('annotation_provenance', 'denominators', 'latency_ms', 'mask_iou')}, indent=2)) + '</pre><h2>Failure examples</h2>' + ''.join(examples) + '<h2>Limitations</h2><ul>' + ''.join('<li>' + html.escape(value) + '</li>' for value in report['limitations']) + '</ul></html>'
    output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    output.with_suffix(".html").write_text(report_html, encoding="utf-8")
    print(json.dumps({"report": str(output), "macro_f1": metrics["macro_f1"], "defect_recall": recall, "false_reject_rate": acceptable_rate, "manual_review_rate": metrics["manual_review_rate"], "targets": targets}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/benchmark.jsonl"))
    parser.add_argument("--profile", type=Path, default=Path("data/calibrated-profile.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/test-evaluation.json"))
    args = parser.parse_args()
    try:
        evaluate(args.manifest, args.profile, args.output)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
