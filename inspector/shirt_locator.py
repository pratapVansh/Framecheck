"""Opt-in shirt localization. DIP owns verdicts; neural regions have no validated thresholds."""
from functools import lru_cache
from dataclasses import fields
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

from inspector.garments import load_shirt_model
from inspector.images import decode_image, png_bytes
from inspector.models import CheckResult, InspectionResult, PhotoContext, Status
from inspector.pipeline import inspect_image
from inspector.regions import analysis_image, region_measurements

CHECKPOINT = Path(__file__).resolve().parents[1] / "models/segformer_b2_clothes"
MODEL_LOCK = threading.Lock()
MASK_CHECKS = frozenset({"blur", "low_contrast", "noise", "underexposure", "overexposure",
                         "background_color", "background_uniformity", "frame_coverage",
                         "placement", "margins", "possible_cropping"})


@lru_cache(maxsize=1)
def _model():
    predict, metadata = load_shirt_model(CHECKPOINT)
    if predict is None:
        raise RuntimeError("Local shirt checkpoint is missing. No download or random model was used.")
    return predict, metadata


def candidate_status(mask, rgb, context):
    context = PhotoContext.parse(context)
    if not isinstance(mask, np.ndarray) or mask.dtype != np.bool_ or mask.shape != rgb.shape[:2]:
        raise ValueError("Shirt prediction must be a native-size boolean mask.")
    if not mask.any():
        return "empty", "No shirt pixels were located."
    if mask.all():
        return "ambiguous", "The prediction covers the entire photograph."
    shape = analysis_image(rgb).shape[1::-1]
    small = cv2.resize(mask.astype(np.uint8), shape, interpolation=cv2.INTER_NEAREST)
    _, _, stats, _ = cv2.connectedComponentsWithStats(small, 8)
    # shortcut: components cannot identify touching shirts; keep all candidate verdicts guarded until instance validation.
    significant = sum(area >= max(16, int(small.sum()) * .01) for area in stats[1:, cv2.CC_STAT_AREA])
    if context == PhotoContext.UNCERTAIN or significant > 1:
        return "ambiguous", "The intended shirt is uncertain or the mask has multiple substantial regions."
    if not significant:
        return "insufficient_pixels", "The located region is too small at inspection resolution."
    return "candidate", "One shirt-shaped region was located; its identity and boundaries are unverified."


def shirt_targets(mask):
    """Offer separate substantial components, ordered left to right; touching shirts remain unresolved."""
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    minimum_area = max(16, int(mask.sum()) * .01)
    components = [index for index in range(1, len(stats))
                  if stats[index, cv2.CC_STAT_AREA] >= minimum_area]
    components.sort(key=lambda index: (stats[index, cv2.CC_STAT_LEFT], stats[index, cv2.CC_STAT_TOP]))
    targets = [{"id": number, "component": index,
                "bbox": [int(value) for value in stats[index, :4]]}
               for number, index in enumerate(components, 1)]
    return labels, targets


def apply_shirt_candidate(result, rgb, mask, metadata):
    """Apply the same safeguards to live predictions and frozen pilot predictions."""
    if mask is None:
        state, reason = "unavailable", "Shirt localization is unavailable. " + metadata.get("error", "")
    else:
        state, reason = candidate_status(mask, rgb, result.photo_context)
    targets = []
    if mask is not None and state == "ambiguous":
        _, targets = shirt_targets(mask)
    if mask is not None and mask.any() and metadata.get("selected_target") is not None:
        state = "selected"
        reason = f"Shirt {metadata['selected_target']} selected by user. Its boundaries remain unverified."
        targets = []
    usable = state in {"candidate", "selected"}
    measurements = region_measurements(rgb, mask, include_product=usable)
    measurements["product_reason"] = None if usable else reason
    code = "experimental_shirt_uncalibrated" if usable else "shirt_locator_" + state
    explanation = ("Experimental shirt measurements have no validated quality or layout thresholds. "
                   "Product-dependent decisions require manual review." if usable else reason +
                   " Checks that need a shirt mask require manual review.")
    baseline_segmentation = result.segmentation.get("baseline_DIP", result.segmentation)
    for check in result.checks:
        if check.identifier in MASK_CHECKS and check.status != Status.DISABLED:
            check.status = Status.REVIEW
            check.reason_code = code
            check.explanation = explanation
            check.measurement = {"score": None, "scope": "experimental_shirt", "available": usable}
            check.threshold = {"calibrated_for_shirt_locator": False}
    display_mask = mask if mask is not None else np.zeros(rgb.shape[:2], bool)
    overlay = rgb.copy()
    overlay[display_mask] = (.65 * overlay[display_mask] + .35 * np.array([0, 180, 160])).astype(np.uint8)
    result.mask_png = png_bytes(display_mask.astype(np.uint8) * 255)
    result.overlay_png = png_bytes(overlay)
    if not result.pipeline_version.endswith("+experimental-shirt-v1"):
        result.pipeline_version += "+experimental-shirt-v1"
    result.segmentation = {"method": "segformer_b2_clothes_experimental", "reliable": False,
                           "product_scope_confirmed": False, "bbox": None,
                           "shirt_locator": {**metadata, "status": state, "reason": reason,
                                             "human_verified": False, "verified_iou": None,
                                             "targets": [{"id": t["id"], "bbox": t["bbox"]} for t in targets],
                                             "instance_selection_validated": False},
                           "DIP_regions": measurements, "baseline_DIP": baseline_segmentation}
    result.notes.append("Opt-in experimental shirt localization. Mask/overlay show an unverified neural candidate, not a verified product boundary.")
    result.notes.append("DIP runs native resolution and independent whole-photo diagnostics. Shirt-dependent decisions lack validated mask/scope thresholds.")
    return result


def select_shirt_target(record, target_id):
    """Create an immutable child inspection from saved native pixels; never rerun the neural model."""
    started = time.perf_counter()
    saved = json.loads(record["result_json"])
    locator = saved["segmentation"].get("shirt_locator", {})
    if locator.get("status") != "ambiguous":
        raise ValueError("This inspection does not have an ambiguous shirt target.")
    mask = np.asarray(Image.open(BytesIO(record["mask_png"])).convert("L")) > 127
    labels, targets = shirt_targets(mask)
    chosen = next((target for target in targets if target["id"] == target_id), None)
    if len(targets) < 2 or chosen is None:
        raise ValueError("Choose one of the numbered shirt targets.")
    args = {field.name: saved[field.name] for field in fields(InspectionResult) if field.name in saved}
    args.update(checks=[CheckResult(**{**check, "status": Status(check["status"])}) for check in saved["checks"]],
                notes=list(saved["notes"]), photo_context=PhotoContext.parse(saved["photo_context"]),
                original_png=record["original_png"], mask_png=record["mask_png"], overlay_png=record["overlay_png"])
    result = InspectionResult(**args)
    result.inspection_id = str(uuid4())
    result.created_at = datetime.now(timezone.utc).isoformat()
    rgb = np.asarray(Image.open(BytesIO(record["original_png"])).convert("RGB"))
    metadata = {key: value for key, value in locator.items()
                if key not in {"status", "reason", "targets", "locator_call_ms_including_first_load"}}
    metadata.update(selected_target=target_id, source_inspection_id=saved["inspection_id"],
                    inference_rerun=False, selection_method="user selected a connected mask region")
    apply_shirt_candidate(result, rgb, labels == chosen["component"], metadata)
    result.notes = list(dict.fromkeys(result.notes))
    result.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return result


def inspect_with_shirt_locator(image_bytes, profile=None, *, photo_context=PhotoContext.UNCERTAIN):
    started = time.perf_counter()
    result = inspect_image(image_bytes, profile, photo_context=photo_context)
    rgb, _ = decode_image(image_bytes)
    mask, metadata = None, {"device": "cpu", "experimental": True}
    inference_started = time.perf_counter()
    try:
        # Serialize loading and inference to bound concurrent CPU/memory demand.
        with MODEL_LOCK:
            predict, model_info = _model()
            mask = predict(rgb)
            candidate_status(mask, rgb, result.photo_context)
        metadata.update(model_info)
    except (ImportError, OSError, ValueError, RuntimeError, KeyError) as error:
        mask = None
        metadata["error"] = f"{type(error).__name__}: {error}"
    metadata["locator_call_ms_including_first_load"] = (time.perf_counter() - inference_started) * 1000
    apply_shirt_candidate(result, rgb, mask, metadata)
    result.duration_ms = round((time.perf_counter() - started) * 1000, 2)
    return result
