"""Train-only candidates, validation-only selection, and a new guarded profile."""
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

from inspector import PIPELINE_VERSION
from inspector.benchmark import development_digest, image_bytes, read_manifest
from inspector.models import InspectionProfile
from inspector.pipeline import classify, measure_image
from scripts.prepare_quality_revision import DATA, REPORT, ROOT

FIELDS = {
    "noise": [("max_noise", "noise")],
    "low_contrast": [("min_contrast", "contrast")],
    "blur": [("min_sharpness", "laplacian_sobel_ratio")],
    "underexposure": [("max_dark_fraction", "dark_fraction"), ("min_exposure_luminance", "median_luminance")],
    "overexposure": [("max_bright_fraction", "bright_fraction"), ("max_exposure_luminance", "median_luminance")],
}
BANDS = (0.0, 0.06, 0.12, 0.20, 0.35, 0.50)


def check_metrics(rows, checks, identifier):
    known = [(row, check) for row, check in zip(rows, checks) if row["labels"][identifier] is not None]
    positives = [(row, check) for row, check in known if row["labels"][identifier]]
    negatives = [(row, check) for row, check in known if not row["labels"][identifier]]
    tp = sum(check.status == "Fail" for _, check in positives)
    fp = sum(check.status == "Fail" for _, check in negatives)
    reviews = sum(check.status == "Manual Review" for _, check in known)
    return {"f1": 2*tp/(len(positives)+tp+fp) if positives else 0.0,
            "precision": tp/(tp+fp) if tp+fp else None,
            "recall": tp/len(positives) if positives else None,
            "false_reject_rate": fp/len(negatives) if negatives else None,
            "review_rate": reviews/len(known) if known else None,
            "known_images": len(known), "positive_images": len(positives), "negative_images": len(negatives),
            "positive_groups": len({r["group_id"] for r,_ in positives}),
            "negative_groups": len({r["group_id"] for r,_ in negatives}),
            "true_positives": tp, "false_positives": fp, "reviews": reviews}


def feasible(metrics):
    return (metrics["positive_groups"] >= 5 and metrics["negative_groups"] >= 5
            and metrics["false_reject_rate"] <= .10 and metrics["review_rate"] <= .30)


def rank(metrics):
    return (feasible(metrics), metrics["f1"], metrics["recall"] or 0,
            -(metrics["false_reject_rate"] if metrics["false_reject_rate"] is not None else 1),
            -(metrics["review_rate"] if metrics["review_rate"] is not None else 1))


def eligible(row, measured, profile):
    short, long = sorted(measured.rgb.shape[:2])
    return (row.get("photo_context") == "isolated_product" and measured.segmentation.reliable
            and measured.quality.get("usable_pixels", 0) >= 32
            and profile.calibration_short_min <= short <= profile.calibration_short_max
            and profile.calibration_long_min <= long <= profile.calibration_long_max)


def main():
    target = ROOT / "data/quality-v3-profile.json"
    assert not target.exists() and not target.with_suffix(".calibration.json").exists(), "Use a new profile version"
    rows = read_manifest(DATA / "development.jsonl")
    assert not any(row["split"] == "test" for row in rows)
    profile = InspectionProfile(name="Guarded small-photo quality v3", version="quality-v3",
        sharpness_metric="laplacian_sobel_ratio", min_sharpness=.1, max_blur_noise=8.0,
        calibration_short_min=60, calibration_short_max=96, calibration_long_min=80, calibration_long_max=96)
    measurements = [measure_image(image_bytes(row)) for row in rows]
    train = [(row, measured) for row, measured in zip(rows, measurements) if row["split"] == "train" and eligible(row, measured, profile)]
    val = [(row, measured) for row, measured in zip(rows, measurements) if row["split"] == "validation" and eligible(row, measured, profile)]
    train_groups, val_groups = {r["group_id"] for r,_ in train}, {r["group_id"] for r,_ in val}
    assert train_groups.isdisjoint(val_groups)
    auc = {}
    for split, selected in (("train",train),("validation",val)):
        known = [(r,m) for r,m in selected if r["labels"]["blur"] is not None
                 and m.quality.get("contrast",0)>=15 and m.quality.get("noise",999)<=8]
        auc[split] = {key:float(roc_auc_score([not r["labels"]["blur"] for r,_ in known], [m.quality[key] for _,m in known]))
                      for key in ("sharpness","laplacian_sobel_ratio")}
    trace = []
    for identifier, parameters in FIELDS.items():
        subset = [(r,m) for r,m in val if r["labels"][identifier] is not None]
        reference = [r for r,_ in subset]

        def evaluate(candidate):
            checks = [next(c for c in classify(m,candidate,photo_context="isolated_product") if c.identifier==identifier) for _,m in subset]
            return check_metrics(reference,checks,identifier)

        best = evaluate(profile)
        for field, measurement in parameters:
            observed = [m.quality[measurement] for r,m in train if measurement in m.quality and r["labels"][identifier] is not None]
            assert observed, (identifier,measurement)
            candidates = sorted(set([getattr(profile,field), *map(float,np.percentile(observed,[5,10,20,30,40,50,60,70,80,90,95]))]))
            search = []
            for value in candidates:
                for band in BANDS:
                    candidate = replace(profile, **{field:value}, quality_review_bands={**profile.quality_review_bands,identifier:band})
                    try:
                        candidate.validate()
                    except ValueError:
                        continue
                    metrics = evaluate(candidate)
                    search.append({"cutoff":value,"band":band,"metrics":metrics})
                    if rank(metrics)>rank(best):
                        profile,best=candidate,metrics
            trace.append({"check":identifier,"field":field,"candidates":search,"selected":getattr(profile,field),
                          "band":profile.quality_review_bands.get(identifier,profile.review_band),"validation":best})
        if not feasible(best):
            profile.quality_review_reasons[identifier] = (
                f"No validation-supported {identifier.replace('_',' ')} rule met the minimum group support, false-reject <=10% and review <=30% constraints. "
                "A score is available, but this check needs manual review; independent checks remain active.")
        if (best["precision"] or 0) < .80:
            profile.quality_review_reasons[identifier] = (
                f"{identifier.replace('_',' ').capitalize()} calibration has insufficient reliable positive detections "
                "(validation precision below 80% or no detections). This check needs review; independent checks remain active.")
        elif (best["recall"] or 0) < .80:
            profile.quality_pass_review_reasons[identifier] = (
                f"The score does not establish absence of {identifier.replace('_',' ')}: validation recall was {best['recall']:.1%}, below 80%. "
                "A potential Pass needs manual review; independently supported defects can still fail.")
        print(identifier, "cutoffs", {field:getattr(profile,field) for field,_ in parameters}, "band",profile.quality_review_bands.get(identifier,profile.review_band),"validation",best,flush=True)
    digest = development_digest(rows)
    profile.calibrated=True;profile.calibration_id=digest[:16]
    profile.validate();target.write_text(json.dumps(profile.to_dict(),indent=2,allow_nan=False),encoding="utf-8")
    report = {"pipeline_version":PIPELINE_VERSION,"profile_sha256":hashlib.sha256(target.read_bytes()).hexdigest(),
              "development_sha256":digest,"training_images":sum(r["split"]=="train" for r in rows),
              "validation_images":sum(r["split"]=="validation" for r in rows),"test_images_used":0,
              "eligible_training_images":len(train),"eligible_validation_images":len(val),
              "training_groups":sorted(train_groups),"validation_groups":sorted(val_groups),
              "calibration_dimensions":{key:getattr(profile,key) for key in ("calibration_short_min","calibration_short_max","calibration_long_min","calibration_long_max")},
              "validation_constraints_met":not profile.quality_review_reasons and not profile.quality_pass_review_reasons,
              "pipeline_files_sha256":{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((ROOT/"inspector").glob("*.py"))},
              "auc_comparison":auc,"search":trace,
              "selection_policy":"Train-only percentiles; validation-only feasible/F1/recall/false-reject/review ranking. Eligibility-specific quality calibration; fixed compliance rules. Failed checks abstain individually.",
              "annotation_provenance":"Inherited AI-reviewed development labels; no human verification. Exact pixel padding inherits quality labels only.",
              "limits":["No normal-sized calibration data","96x96 development inputs are padded thumbnail controls","Five positive/negative validation groups is a minimum, not a population guarantee","No compliance calibration or production approval"],
              "default_promotion_approved":False}
    target.with_suffix(".calibration.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    freeze={"profile":str(target.relative_to(ROOT)),"profile_sha256":report["profile_sha256"],
            "calibration_sha256":hashlib.sha256(target.with_suffix(".calibration.json").read_bytes()).hexdigest(),
            "split_freeze_sha256":hashlib.sha256((DATA/"split.freeze.json").read_bytes()).hexdigest(),
            "code_sha256":{**report["pipeline_files_sha256"],str(Path(__file__).resolve()):hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            "test_evaluated":False,"fixed_test_policy":"Definitive Fail positives; unknown truth excluded per check; Review is a miss for true defects; no retuning after test."}
    (REPORT/"method.freeze.json").write_text(json.dumps(freeze,indent=2),encoding="utf-8")
    print("New profile and method frozen; no test images measured.",flush=True)


if __name__ == "__main__":
    main()
