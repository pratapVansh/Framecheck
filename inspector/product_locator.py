"""Category-safe localization, explicit target marking, and DIP evidence per scope."""
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import time
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageDraw

from inspector.images import png_bytes
from inspector.models import InspectionProfile, InspectionResult, PhotoContext, Status
from inspector.pipeline import classify, measure_image
from inspector.product_measurements import measure_scope
from inspector.shirt_locator import MASK_CHECKS, MODEL_LOCK, _model, candidate_status, shirt_targets

CATEGORIES = {"general", "footwear", "clothing"}
MODES = {"auto", "foreground", "manual", "segformer"}
QUALITY_REGIONS = {"blur", "low_contrast", "noise", "underexposure", "overexposure"}


def route(category, mode, context):
    context = PhotoContext.parse(context)
    if category not in CATEGORIES or mode not in MODES:
        raise ValueError("Choose a supported product category and locator.")
    if mode == "segformer" and category != "clothing":
        raise ValueError("SegFormer supports clothing only. Use general foreground or mark the product.")
    if mode != "auto":
        return mode
    if context == PhotoContext.ISOLATED_PRODUCT:
        return "foreground"
    return "segformer" if category == "clothing" and context == PhotoContext.MODEL_WORN else "manual"


def _finish(image_bytes, measured, profile, context, mask, locator, *, native_hash=None):
    rgb = measured.rgb
    available = locator["status"] in {"candidate", "selected"}
    if locator["resolved_mode"] == "foreground" and locator["status"] == "candidate":
        scoped = measured
    else:
        scoped = measure_scope(rgb, mask, reliable=available,
                               reason=locator["reason"] if not available else "")
    # A resolved target removes the broad photo-context guard, not individual evidence guards.
    checks = classify(scoped, profile, photo_context=PhotoContext.ISOLATED_PRODUCT)
    for check in checks:
        if check.identifier not in MASK_CHECKS or check.status == Status.DISABLED:
            continue
        if not available:
            check.status = Status.REVIEW
            check.reason_code = "product_target_unresolved"
            check.explanation = locator["reason"] + " This check needs a specific product region; mark its outline."
            check.measurement = {"score": None, "scope": "product"}
        elif locator["resolved_mode"] != "foreground" and check.identifier in QUALITY_REGIONS:
            if check.status != Status.REVIEW:
                check.status = Status.REVIEW
                check.reason_code = "product_mask_calibration_unavailable"
                check.explanation = "The product measurement ran, but this profile has no validated quality cutoffs for this locator's mask scope."
        elif context == PhotoContext.MODEL_WORN and check.identifier in {"background_color", "background_uniformity"}:
            check.status = Status.REVIEW
            check.reason_code = "scene_background_unseparated"
            check.explanation = "Pixels outside the product also contain a person. Scene background is not separately located for this check."
    whole = measure_scope(rgb, np.ones(rgb.shape[:2], bool)).quality
    whole["threshold_status"] = "measurement_only_no_whole_photo_quality_calibration"
    whole["reason"] = "Product-region quality cutoffs do not apply to the whole photo. Native resolution retains its independent verdict."
    if whole.get("noise") is None:
        whole["noise_unavailable_reason"] = "Too few flat whole-photo pixels for noise estimation."
    whole_checks = [check.to_dict() for check in checks if check.identifier not in MASK_CHECKS]
    overlay = rgb.copy()
    overlay[mask] = (.65 * overlay[mask] + .35 * np.array([0, 180, 160])).astype(np.uint8)
    h, w = rgb.shape[:2]
    bbox = scoped.geometry.get("bbox") if available else None
    segmentation = {"method": "category_product_locator_v1", "requested_locator": locator["requested_mode"],
        "product_locator": locator, "reliable": available, "human_verified": False,
        "product_scope_confirmed": available, "bbox": bbox,
        "whole_photo": {"checks": whole_checks, "diagnostics": whole},
        "product_diagnostics": scoped.quality if available else None}
    notes = list(measured.notes) + ["DIP assigns the final verdict. Rules are demonstrations, not marketplace standards.",
        "Automatic masks and marked outlines are unverified. Target selection is not a verified reference annotation."]
    return InspectionResult(str(uuid4()), datetime.now(timezone.utc).isoformat(),
        native_hash or hashlib.sha256(image_bytes).hexdigest(), w, h, "0.4.0+product-locator-v1",
        profile.to_dict(), context, checks, notes, segmentation, 0,
        png_bytes(rgb), png_bytes(mask.astype(np.uint8) * 255), png_bytes(overlay))


def inspect_product(image_bytes, profile=None, *, photo_context=PhotoContext.UNCERTAIN,
                    category="general", mode="auto"):
    started = time.perf_counter()
    context = PhotoContext.parse(photo_context)
    resolved = route(category, mode, context)
    profile = (profile or InspectionProfile()).validate()
    measured = measure_image(image_bytes)
    rgb = measured.rgb
    mask = np.zeros(rgb.shape[:2], bool)
    locator = {"category": category, "requested_mode": mode, "resolved_mode": resolved,
               "status": "needs_target", "reason": "Mark the intended product because its target is unclear.",
               "targets": [], "verified_iou": None}
    if resolved == "foreground":
        mask = cv2.resize(measured.segmentation.mask.astype(np.uint8), rgb.shape[1::-1], interpolation=cv2.INTER_NEAREST).astype(bool)
        _, targets = shirt_targets(mask)
        if not mask.any():
            locator.update(status="empty", reason="General foreground segmentation found no usable product pixels.")
        elif context != PhotoContext.ISOLATED_PRODUCT or len(targets) != 1:
            locator.update(status="ambiguous", reason="General foreground does not establish one intended product in this scene.")
        elif not measured.segmentation.reliable:
            locator.update(status="uncertain", reason="General foreground evidence is weak: " + " ".join(measured.segmentation.reasons))
        else:
            locator.update(status="candidate", reason="General foreground located one isolated product candidate.")
        locator["targets"] = [{"id": t["id"], "bbox": t["bbox"]} for t in targets] if len(targets)>1 else []
    elif resolved == "segformer":
        try:
            with MODEL_LOCK:
                predict, info = _model()
                mask = predict(rgb)
            status, reason = candidate_status(mask, rgb, context)
            locator.update(status=status, reason=reason, model=info)
            if status == "ambiguous":
                _, targets = shirt_targets(mask)
                locator["targets"] = [{"id": t["id"], "bbox": t["bbox"]} for t in targets]
        except (ImportError, OSError, RuntimeError, ValueError, KeyError) as error:
            mask = np.zeros(rgb.shape[:2], bool)
            locator.update(status="unavailable", reason=f"Clothing locator unavailable ({type(error).__name__}). Mark the product to continue.")
    result = _finish(image_bytes, measured, profile, context, mask, locator)
    result.duration_ms = round((time.perf_counter()-started)*1000, 2)
    return result


def _source(record):
    saved = json.loads(record["result_json"])
    locator = saved["segmentation"].get("product_locator")
    if not locator:
        raise ValueError("Open a current product-locator inspection before marking its target.")
    rgb = np.asarray(Image.open(BytesIO(record["original_png"])).convert("RGB"))
    return saved, locator, rgb


def polygon_mask(points, size):
    if not isinstance(points, list) or not 3 <= len(points) <= 200:
        raise ValueError("Mark a product outline with 3 to 200 points.")
    width, height = size
    for point in points:
        if (not isinstance(point, list) or len(point)!=2 or
            any(isinstance(v,bool) or not isinstance(v,(int,float)) or not np.isfinite(v) for v in point) or
            not 0 <= point[0] < width or not 0 <= point[1] < height):
            raise ValueError("Every outline point must lie inside the original photo.")
    vertices = np.array(points, np.float32)
    if abs(cv2.contourArea(vertices)) < 16:
        raise ValueError("The marked outline is too small or has no area.")
    image = Image.new("L", (width,height), 0)
    ImageDraw.Draw(image).polygon([tuple(point) for point in points],fill=255)
    mask = np.asarray(image)>0
    if mask.all():
        raise ValueError("Mark the product outline rather than the entire photo.")
    return mask


def select_product(record, *, target_id=None, polygon=None):
    started=time.perf_counter()
    saved, previous, rgb = _source(record)
    if polygon is not None and target_id is not None:
        raise ValueError("Choose a detected product or mark its outline, not both.")
    if polygon is not None:
        mask=polygon_mask(polygon,(rgb.shape[1],rgb.shape[0]))
        reason="User marked the visible product outline. Fine boundaries remain unverified."
        mode="manual"
    else:
        original=np.asarray(Image.open(BytesIO(record["mask_png"])).convert("L"))>127
        labels, targets=shirt_targets(original)
        chosen=next((t for t in targets if t["id"]==target_id),None)
        if chosen is None or previous["status"] not in {"ambiguous","uncertain"}:
            raise ValueError("Choose one of the displayed product targets.")
        mask=labels==chosen["component"]
        reason=f"Product {target_id} selected by user. Fine boundaries remain unverified."
        mode="manual"
    # Stored native PNGs can exceed the upload byte cap; these pixels were already decoded safely.
    shell=measure_scope(rgb,mask)
    profile=InspectionProfile.from_dict(saved["profile"])
    locator={**previous,"status":"selected","reason":reason,"resolved_mode":mode,"targets":[],
        "source_inspection_id":saved["inspection_id"],"selected_target":target_id,
        "selection_method":"polygon" if polygon is not None else "component","inference_rerun":False}
    result=_finish(b"",shell,profile,PhotoContext.parse(saved["photo_context"]),mask,locator,native_hash=saved["input_hash"])
    result.duration_ms=round((time.perf_counter()-started)*1000,2)
    return result
