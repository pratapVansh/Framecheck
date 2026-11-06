"""Develop thresholds on train/validation only; freeze a versioned profile."""
import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np

from inspector import PIPELINE_VERSION
from inspector.benchmark import development_digest, image_bytes, read_manifest, rules_hash, summarize_predictions
from inspector.models import InspectionProfile, PhotoContext, aggregate
from inspector.pipeline import classify, measure_image


def predict(measurements, profile, rows):
    if len(measurements) != len(rows):
        raise ValueError("Measurements and photo-context records must have matching lengths.")
    predictions = []
    for value, row in zip(measurements, rows):
        context = PhotoContext.parse(row.get("photo_context", PhotoContext.UNCERTAIN))
        checks = classify(value, profile, photo_context=context)
        predictions.append({"status": aggregate(checks), "photo_context": context, "checks": {check.identifier: check.status for check in checks}})
    return predictions


def calibrated_rank(metrics):
    # Missing acceptable examples cannot establish a false reject constraint.
    feasible = metrics["false_reject_rate"] is not None and metrics["false_reject_rate"] <= 0.10 and metrics["manual_review_rate"] <= 0.30
    return (int(feasible), metrics["macro_f1"], metrics["defect_recall"] or 0.0, -(metrics["false_reject_rate"] if metrics["false_reject_rate"] is not None else 1.0), -metrics["manual_review_rate"])


def calibrate(manifest, output):
    if output.exists() or output.with_suffix(".calibration.json").exists():
        raise ValueError("A frozen profile/report already exists. Choose a new output version.")
    rows = read_manifest(manifest)
    training = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    if not training or not validation or not all(row["reviewed"] for row in training + validation):
        raise ValueError("All train and validation rows must be independently reviewed; both splits are required.")
    profile = InspectionProfile()
    if any(row["rules_hash"] != rules_hash(profile) for row in training + validation):
        raise ValueError("Ground-truth rules do not match the demonstration profile.")
    train_measurements = [measure_image(image_bytes(row)) for row in training]
    val_measurements = [measure_image(image_bytes(row)) for row in validation]
    product_measurements = [value for row, value in zip(training, train_measurements)
                            if row.get("photo_context") == PhotoContext.ISOLATED_PRODUCT]
    originals = [value for row, value in zip(training, train_measurements)
                 if row.get("kind") == "original" and row.get("photo_context") == PhotoContext.ISOLATED_PRODUCT]
    if not originals:
        raise ValueError("Reviewed isolated-product training originals with confirmed photo context are needed for calibration.")
    short_sides = [min(value.rgb.shape[:2]) for value in originals]
    long_sides = [max(value.rgb.shape[:2]) for value in originals]
    profile.calibration_short_min, profile.calibration_short_max = min(short_sides), max(short_sides)
    profile.calibration_long_min, profile.calibration_long_max = min(long_sides), max(long_sides)
    eligible_measurements = [value for value in product_measurements if value.segmentation.reliable
                            and value.quality.get("usable_pixels", 0) >= 32
                            and profile.calibration_short_min <= min(value.rgb.shape[:2]) <= profile.calibration_short_max
                            and profile.calibration_long_min <= max(value.rgb.shape[:2]) <= profile.calibration_long_max]
    baseline = summarize_predictions(validation, predict(val_measurements, profile, validation))
    best = baseline
    trace = []
    parameters = {
        "min_sharpness": "sharpness", "min_contrast": "contrast", "max_noise": "noise",
        "max_dark_fraction": "dark_fraction", "max_bright_fraction": "bright_fraction",
        "min_exposure_luminance": "median_luminance", "max_exposure_luminance": "median_luminance",
    }
    # Two fixed coordinate-search passes are deterministic and bound calibration work.
    for iteration in range(2):
        for field, measurement_key in parameters.items():
            observed = [value.quality[measurement_key] for value in eligible_measurements if measurement_key in value.quality]
            candidates = sorted(set([getattr(profile, field)] + ([float(x) for x in np.percentile(observed, [10, 30, 50, 70, 90])] if observed else [])))
            evaluated = []
            for value in candidates:
                candidate = replace(profile, **{field: value})
                try:
                    candidate.validate()
                except ValueError:
                    continue
                metrics = summarize_predictions(validation, predict(val_measurements, candidate, validation))
                evaluated.append({"value": value, "macro_f1": metrics["macro_f1"],
                                  "false_reject_rate": metrics["false_reject_rate"],
                                  "manual_review_rate": metrics["manual_review_rate"],
                                  "constraints_met": bool(calibrated_rank(metrics)[0])})
                if calibrated_rank(metrics) > calibrated_rank(best):
                    profile, best = candidate, metrics
            trace.append({"iteration": iteration, "field": field, "chosen": getattr(profile, field),
                          "macro_f1": best["macro_f1"], "candidates": evaluated})
        evaluated = []
        for band in (0.0, 0.06, 0.12, 0.20):
            candidate = replace(profile, review_band=band)
            metrics = summarize_predictions(validation, predict(val_measurements, candidate, validation))
            evaluated.append({"value": band, "macro_f1": metrics["macro_f1"],
                              "false_reject_rate": metrics["false_reject_rate"],
                              "manual_review_rate": metrics["manual_review_rate"],
                              "constraints_met": bool(calibrated_rank(metrics)[0])})
            if calibrated_rank(metrics) > calibrated_rank(best):
                profile, best = candidate, metrics
        trace.append({"iteration": iteration, "field": "review_band", "chosen": profile.review_band,
                      "macro_f1": best["macro_f1"], "candidates": evaluated})
    digest = development_digest(training + validation)
    profile.calibrated = True
    profile.calibration_id = digest[:16]
    profile.version = "calibrated-" + profile.calibration_id
    profile.validate()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile.to_dict(), indent=2, allow_nan=False), encoding="utf-8")
    profile_digest = hashlib.sha256(output.read_bytes()).hexdigest()
    report = {
        "pipeline_version": PIPELINE_VERSION, "profile_sha256": profile_digest,
        "development_sha256": digest,
        "training_images": len(training), "validation_images": len(validation), "test_images_used": 0,
        "isolated_product_training_images": len(product_measurements),
        "eligible_training_measurements": len(eligible_measurements),
        "training_groups": sorted({row["group_id"] for row in training}),
        "validation_groups": sorted({row["group_id"] for row in validation}),
        "baseline_validation": baseline, "selected_validation": best, "search": trace,
        "validation_constraints_met": bool(calibrated_rank(best)[0]),
        "calibration_dimensions": {key: getattr(profile, key) for key in (
            "calibration_short_min", "calibration_short_max", "calibration_long_min", "calibration_long_max")},
        "annotation_provenance": {split: {
            "methods": dict(Counter(row.get("review_method", "unrecorded") for row in selected)),
            "human_verified_images": sum(bool(row.get("human_verified")) for row in selected),
            "unknown_labels": sum(value is None for row in selected for value in row["labels"].values()),
        } for split, selected in (("train", training), ("validation", validation))},
        "calibrator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pipeline_files_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in sorted(Path(__file__).resolve().parents[1].joinpath("inspector").glob("*.py"))},
        "search_policy": "Two coordinate passes; candidate percentiles 10/30/50/70/90 from reliable, adequately sampled, in-range isolated-product training measurements plus current cutoffs; review bands 0/0.06/0.12/0.20; feasibility then macro-F1, recall, lower false rejects/reviews; strict rank improvement; fixed compliance rules.",
        "limitations": "Development-calibrated does not mean targets were met or test performance was established. Candidate cutoffs come from training; selection uses validation only. Geometry and background demonstration rules remain fixed.",
    }
    output.with_suffix(".calibration.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Saved frozen profile: {output}. Validation constraints met: {report['validation_constraints_met']}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/benchmark.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/calibrated-profile.json"))
    args = parser.parse_args()
    try:
        calibrate(args.manifest, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
