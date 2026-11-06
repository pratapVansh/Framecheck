"""Bounded development-only DIP alternative; not the application's default.

Uses existing masks with an additional edge/stability eligibility hypothesis.
It cannot bypass context, supported dimensions, blur confounding, or cropping advice.
"""
from dataclasses import replace

import cv2
import numpy as np

from inspector.models import InspectionProfile, PhotoContext, Status
from inspector.pipeline import Measurements, classify


def edge_measurements(measured: Measurements) -> dict:
    gray = cv2.cvtColor(measured.analysis, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    energy = gx * gx + gy * gy
    kernel = np.ones((3, 3), np.uint8)
    mask = measured.segmentation.mask.astype(np.uint8)
    interior = cv2.erode(mask, kernel).astype(bool)
    interior[[0, -1], :] = False
    interior[:, [0, -1]] = False
    boundary = cv2.dilate(mask, kernel).astype(bool) & ~interior
    smooth = cv2.GaussianBlur(gray, (5, 5), 1)
    gradient = cv2.magnitude(cv2.Sobel(smooth, cv2.CV_32F, 1, 0), cv2.Sobel(smooth, cv2.CV_32F, 0, 1))
    support = float(np.mean(gradient[boundary] >= 25)) if boundary.any() else 0.0
    d = measured.segmentation.diagnostics
    edge_supported = (0.01 < d.get("foreground_fraction", 0) < 0.95
                      and d.get("stability_iou", 0) >= .90 and d.get("refinement_iou", 0) >= .85
                      and support >= .60 and measured.quality.get("usable_pixels", 0) >= 32)
    object_ok = measured.segmentation.reliable or edge_supported
    return {"tenengrad": float(np.mean(energy[interior])) if interior.sum() >= 32 else None,
            "boundary_support": support, "edge_supported": bool(edge_supported),
            "object_eligible": bool(object_ok),
            "background_eligible": bool(object_ok and measured.background["pixels"] >= 20),
            "product_pixels": int(interior.sum()), "background_pixels": measured.background["pixels"],
            "analysis_width": measured.analysis.shape[1], "analysis_height": measured.analysis.shape[0],
            "dark_fraction": measured.quality.get("dark_fraction"), "bright_fraction": measured.quality.get("bright_fraction"),
            "policy": "Fixed hypothesis: Sobel >=25 boundary support >=0.60, stability >=0.90, refinement agreement >=0.85. No threshold tuning in Phase 4."}


def classify_alternative(measured: Measurements, profile: InspectionProfile, *, photo_context=PhotoContext.UNCERTAIN):
    diagnostics = edge_measurements(measured)
    object_view = replace(measured, segmentation=replace(measured.segmentation, reliable=diagnostics["object_eligible"]))
    background_view = replace(measured, segmentation=replace(measured.segmentation, reliable=diagnostics["background_eligible"]))
    objects = classify(object_view, profile, photo_context=photo_context)
    backgrounds = {c.identifier:c for c in classify(background_view, profile, photo_context=photo_context)}
    checks = []
    for item in objects:
        check = backgrounds[item.identifier] if item.identifier in {"background_color", "background_uniformity"} else item
        if check.identifier in {"background_color", "background_uniformity"} and check.status == Status.REVIEW and diagnostics["object_eligible"] and not diagnostics["background_eligible"] and PhotoContext.parse(photo_context) == PhotoContext.ISOLATED_PRODUCT:
            check.reason_code = "insufficient_background"
            check.explanation = "Too few background pixels for this check. Product measurements are assessed separately."
        check.measurement = {**check.measurement, "eligibility_method":"edge_supported_v1",
                             "boundary_support":diagnostics["boundary_support"]}
        checks.append(check)
    return checks, diagnostics
