"""Reviewed dataset manifests and honest, abstention-aware multilabel metrics."""

from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import f1_score, precision_score, recall_score

from inspector.models import DEFECTS, InspectionProfile, PhotoContext, Status

RULE_FIELDS = (
    "require_white", "min_short_side", "min_long_side", "white_min_channel", "white_max_spread",
    "min_white_fraction", "max_background_distance", "min_coverage", "max_coverage", "max_center_offset", "min_margin",
)


def development_digest(rows: list[dict]) -> str:
    development = sorted((row for row in rows if row["split"] in {"train", "validation"}), key=lambda row: row["id"])
    return hashlib.sha256(json.dumps(development, sort_keys=True).encode()).hexdigest()


def image_bytes(row: dict) -> bytes:
    data = Path(row["image"]).read_bytes()
    expected = row.get("image_sha256")
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"Image changed after manifest creation: {row['id']}.")
    return data


def rules_hash(profile: InspectionProfile) -> str:
    value = {key: getattr(profile, key) for key in RULE_FIELDS}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def read_manifest(path: str | Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not {"id", "source_id", "group_id", "image", "labels", "reviewed", "split", "rules_hash"} <= row.keys():
            raise ValueError(f"Manifest row {line_number} is missing required fields.")
        if set(row["labels"]) != set(DEFECTS) or any(value is not None and type(value) is not bool for value in row["labels"].values()):
            raise ValueError(f"Invalid labels on manifest row {line_number}.")
        if type(row["reviewed"]) is not bool or not str(row["group_id"]).strip():
            raise ValueError(f"Invalid review flag or group on row {line_number}.")
        if any(not isinstance(row[key], str) or not row[key].strip() for key in ("id", "source_id", "group_id", "image", "rules_hash")):
            raise ValueError(f"Manifest identifiers and paths must be nonempty strings on row {line_number}.")
        if row["split"] not in {None, "train", "validation", "test"}:
            raise ValueError(f"Invalid split on row {line_number}.")
        PhotoContext.parse(row.get("photo_context", PhotoContext.UNCERTAIN))
        rows.append(row)
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Manifest IDs must be unique.")
    assert_group_splits(rows)
    return rows


def write_manifest(path: str | Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in rows), encoding="utf-8")
    temporary.replace(path)


def verify_split_freeze(path: str | Path) -> None:
    path = Path(path)
    report_path = path.with_suffix(".split.json")
    frozen = path.with_suffix(".split-freeze.jsonl.bak")
    if not report_path.exists() or not frozen.exists():
        raise ValueError("Frozen split artifacts are missing; create and freeze source splits before generation.")
    expected = json.loads(report_path.read_text(encoding="utf-8"))["frozen_sha256"]
    if any(hashlib.sha256(item.read_bytes()).hexdigest() != expected for item in (path, frozen)):
        raise ValueError("Source manifest changed after split freeze. Preserve the frozen benchmark and version any corrections.")
    for row in read_manifest(path):
        image_bytes(row)
        if row.get("mask_reviewed") and row.get("mask_sha256"):
            if hashlib.sha256(Path(row["mask"]).read_bytes()).hexdigest() != row["mask_sha256"]:
                raise ValueError(f"Reviewed mask changed after split freeze: {row['id']}.")


def assert_group_splits(rows: list[dict]) -> None:
    groups, sources, images = {}, {}, {}
    for row in rows:
        split = row.get("split")
        if split is None:
            continue
        for registry, key in ((groups, row["group_id"]), (sources, row["source_id"]), (images, row.get("source_hash"))):
            if key is not None and registry.setdefault(key, split) != split:
                raise ValueError(f"Data leakage: related group/source {key} crosses splits.")


def summarize_predictions(rows: list[dict], predictions: list[dict]) -> dict:
    """Only definitive Fail checks count as positives in the primary metrics."""
    if len(rows) != len(predictions):
        raise ValueError("Prediction and ground-truth lengths differ.")
    if not rows:
        raise ValueError("No reviewed evaluation rows are available.")
    metrics, truths, detected = {}, [], []
    for defect in DEFECTS:
        selected = [(row, pred) for row, pred in zip(rows, predictions) if row["labels"][defect] is not None]
        y = [int(row["labels"][defect]) for row, _ in selected]
        yhat = [int(pred["checks"][defect] == Status.FAIL) for _, pred in selected]
        true_groups = {row["group_id"] for row, _ in selected if row["labels"][defect]}
        metrics[defect] = {
            "precision": float(precision_score(y, yhat, zero_division=0)) if y else 0.0,
            "recall": float(recall_score(y, yhat, zero_division=0)) if y else 0.0,
            "f1": float(f1_score(y, yhat, zero_division=0)) if y else 0.0,
            "positive_support": sum(y), "assessed_support": len(y),
            "positive_product_groups": len(true_groups), "unknown_labels": len(rows) - len(y),
            "review_count": sum(pred["checks"][defect] == Status.REVIEW for _, pred in selected),
            "reviewed_true_defects": sum(bool(row["labels"][defect]) and pred["checks"][defect] == Status.REVIEW for row, pred in selected),
        }
        truths.extend(y)
        detected.extend(yhat)
    acceptable = [i for i, row in enumerate(rows) if all(value is False for value in row["labels"].values())]
    defective = [i for i, row in enumerate(rows) if any(value is True for value in row["labels"].values())]
    rejected = sum(predictions[i]["status"] == Status.FAIL for i in acceptable)
    def reviewed_rate(indices):
        return sum(predictions[i]["status"] == Status.REVIEW for i in indices) / len(indices) if indices else None
    positive_total = sum(truths)
    true_detected = sum(bool(y) and bool(yhat) for y, yhat in zip(truths, detected))
    reviews = sum(pred["status"] == Status.REVIEW for pred in predictions)
    cropping = metrics["possible_cropping"]
    return {
        "images": len(rows), "product_groups": len({row["group_id"] for row in rows}),
        "macro_f1": float(np.mean([value["f1"] for value in metrics.values()])),
        "defect_recall": true_detected / positive_total if positive_total else None,
        "true_detected_defects": true_detected, "actual_defect_instances": positive_total,
        "false_reject_rate": rejected / len(acceptable) if acceptable else None,
        "acceptable_images": len(acceptable), "false_rejects": rejected,
        "manual_review_rate": reviews / len(rows), "manual_reviews": reviews,
        "acceptable_manual_review_rate": reviewed_rate(acceptable),
        "defective_manual_review_rate": reviewed_rate(defective),
        "automatic_decision_coverage": 1 - reviews / len(rows),
        "decision_counts": dict(Counter(pred["status"] for pred in predictions)),
        "per_category": metrics,
        "advisory_cropping": {
            "true_cases": cropping["positive_support"],
            "true_cases_referred": cropping["reviewed_true_defects"],
            "referral_recall": cropping["reviewed_true_defects"] / cropping["positive_support"] if cropping["positive_support"] else None,
            "all_referrals": cropping["review_count"],
            "policy": "Referrals are Manual Review dispositions, including photo-context or segmentation uncertainty. They are not definitive detections or rejections. Primary cropping F1/recall remain zero when the advisory check never emits Fail.",
        },
        "complete_category_support": all(value["positive_product_groups"] >= 10 for value in metrics.values()),
        "metric_policy": "Only definitive Fail checks count as detected defects. Unresolved true defects count as missed. Unknown truth is excluded per label. Possible cropping is advisory and never fails on edge contact alone.",
    }


def report_slices(rows: list[dict], predictions: list[dict]) -> dict:
    slices = {}
    for field in ("kind", "severity", "category"):
        values = sorted({str(row.get(field, "original")) for row in rows})
        for value in values:
            indices = [i for i, row in enumerate(rows) if str(row.get(field, "original")) == value]
            slices[f"{field}:{value}"] = summarize_predictions([rows[i] for i in indices], [predictions[i] for i in indices])
    automatic = [i for i, pred in enumerate(predictions) if pred["status"] != Status.REVIEW]
    if automatic:
        slices["secondary:automatic_only"] = summarize_predictions([rows[i] for i in automatic], [predictions[i] for i in automatic])
    return slices


def bootstrap_comparison(rows, methods, iterations=1000, seed=42):
    """Paired source-group bootstrap; keep every variant with its source group."""
    if not rows or not methods or iterations < 1 or any(len(p) != len(rows) for p in methods.values()):
        raise ValueError("Bootstrap requires matching nonempty rows/predictions and positive iterations.")
    groups = sorted({r["group_id"] for r in rows})
    labels = np.array([[r["labels"][d] for d in DEFECTS] for r in rows], dtype=object)
    truth, known = labels == True, labels != None
    acceptable = np.all((labels == False) & known, axis=1)
    matrices = {}
    for name, predictions in methods.items():
        fail = np.array([[p["checks"][d] == Status.FAIL for d in DEFECTS] for p in predictions])
        rejected = np.array([p["status"] == Status.FAIL for p in predictions])
        review = np.array([p["status"] == Status.REVIEW for p in predictions])
        counts = []
        for group in groups:
            selected = np.array([r["group_id"] == group for r in rows])
            counts.append(np.concatenate([
                np.sum((truth & fail & known)[selected], axis=0),
                np.sum((~truth & fail & known)[selected], axis=0),
                np.sum((truth & ~fail & known)[selected], axis=0),
                [np.sum((acceptable & rejected)[selected]),np.sum(acceptable[selected]),np.sum(review[selected]),np.sum(selected)]]))
        matrices[name] = np.array(counts)
    rng = np.random.default_rng(seed)
    draws = {name:[] for name in methods}
    for _ in range(iterations):
        weights = np.bincount(rng.integers(0,len(groups),len(groups)),minlength=len(groups))
        for name, matrix in matrices.items():
            values = weights @ matrix
            tp, fp, fn = values[:12], values[12:24], values[24:36]
            denom = 2*tp + fp + fn
            f1 = float(np.mean(np.divide(2*tp,denom,out=np.zeros(12),where=denom>0)))
            recall = float(tp.sum()/(tp+fn).sum()) if (tp+fn).sum() else float('nan')
            fr = float(values[36]/values[37]) if values[37] else float('nan')
            draws[name].append([f1,recall,fr,float(values[38]/values[39])])
    fields = ('macro_f1','defect_recall','false_reject_rate','manual_review_rate')
    def intervals(array):
        result = {}
        for i,field in enumerate(fields):
            valid = array[:,i][np.isfinite(array[:,i])]
            result[field] = {'lower':float(np.percentile(valid,2.5)) if len(valid) else None,
                             'upper':float(np.percentile(valid,97.5)) if len(valid) else None,
                             'defined_replicates':len(valid)}
        return result
    arrays = {name:np.array(values) for name,values in draws.items()}
    baseline = next(iter(arrays))
    return {'seed':seed,'iterations':iterations,'groups':len(groups),'confidence_level':.95,
            'policy':'Resample source groups with all their originals/variants; identical draws across methods. Undefined denominators excluded and counted.',
            'methods':{name:intervals(array) for name,array in arrays.items()},
            'paired_deltas_from_'+baseline:{name:intervals(array-arrays[baseline]) for name,array in arrays.items() if name!=baseline}}
