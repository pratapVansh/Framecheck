from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import time
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image, ImageDraw

from inspector import PIPELINE_VERSION
from inspector.images import decode_image, png_bytes
from inspector.models import CheckResult, InspectionProfile, InspectionResult, PhotoContext, Status
from inspector.segmentation import Segmentation, lab_image, segment


@dataclass
class Measurements:
    rgb: np.ndarray
    analysis: np.ndarray
    segmentation: Segmentation
    quality: dict
    background: dict
    geometry: dict
    notes: list[str]


def measure_image(image_bytes: bytes) -> Measurements:
    rgb, notes = decode_image(image_bytes)
    h, w = rgb.shape[:2]
    scale = min(1.0, 512 / max(h, w))
    analysis = cv2.resize(rgb, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    seg = segment(analysis)
    gray = cv2.cvtColor(analysis, cv2.COLOR_RGB2GRAY).astype(np.float32)
    interior = cv2.erode(seg.mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    valid = interior & (np.indices(gray.shape)[0] > 0) & (np.indices(gray.shape)[0] < gray.shape[0] - 1) & (np.indices(gray.shape)[1] > 0) & (np.indices(gray.shape)[1] < gray.shape[1] - 1)
    pixels = gray[valid]
    q = {"usable_pixels": int(pixels.size)}
    if pixels.size >= 32:
        low, high = np.percentile(pixels, [5, 95])
        q.update(contrast=float(high - low), median_luminance=float(np.median(pixels)),
                 dark_fraction=float(np.mean(pixels < 25)), bright_fraction=float(np.mean(pixels > 245)),
                 sharpness=float(np.var(cv2.Laplacian(gray, cv2.CV_32F)[valid])))
        gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        q["sobel_energy"] = float(np.mean((gx * gx + gy * gy)[valid]))
        if q["sobel_energy"] > 1e-8:
            q["laplacian_sobel_ratio"] = q["sharpness"] / q["sobel_energy"]
        smooth = cv2.GaussianBlur(gray, (5, 5), 1)
        gradient = cv2.magnitude(cv2.Sobel(smooth, cv2.CV_32F, 1, 0), cv2.Sobel(smooth, cv2.CV_32F, 0, 1))
        flat = valid & (gradient <= min(25.0, float(np.percentile(gradient[valid], 35))))
        q["noise_pixels"] = int(flat.sum())
        if flat.sum() >= 32:
            response = cv2.filter2D(gray, cv2.CV_32F, np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)) / 6
            residual = response[flat]
            q["noise"] = float(np.median(np.abs(residual - np.median(residual))) / 0.67448975)
    bg = ~cv2.dilate(seg.mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    background = {"pixels": int(bg.sum())}
    if bg.sum() >= 20:
        colors = analysis[bg].astype(np.int16)
        lab = lab_image(analysis)[bg]
        background.update(
            # Store the distribution, so profile changes do not reuse a stale white threshold.
            rgb=colors, distance=float(np.percentile(np.linalg.norm(lab - np.median(lab, axis=0), axis=1), 90)),
        )
    geometry = {}
    if seg.bbox:
        x, y, bw, bh = seg.bbox
        ah, aw = analysis.shape[:2]
        margins = [x / aw, y / ah, (aw - x - bw) / aw, (ah - y - bh) / ah]
        edge = seg.raw_mask | seg.mask
        geometry = {
            "coverage": max(bw / aw, bh / ah),
            "center_offset": max(abs((x + bw / 2) / aw - 0.5), abs((y + bh / 2) / ah - 0.5)),
            "margins": margins, "margin_min": min(margins),
            "edge_pixels": int(edge[0].sum() + edge[-1].sum() + edge[1:-1, 0].sum() + edge[1:-1, -1].sum()),
            "bbox": [round(x * w / aw), round(y * h / ah), round(bw * w / aw), round(bh * h / ah)],
        }
    return Measurements(rgb, analysis, seg, q, background, geometry, notes)


def _threshold_status(score: float, threshold: float, minimum: bool, band: float) -> Status:
    tolerance = max(abs(threshold) * band, 1e-8)
    if abs(score - threshold) <= tolerance:
        return Status.REVIEW
    failed = score < threshold if minimum else score > threshold
    return Status.FAIL if failed else Status.PASS


def classify(measured: Measurements, profile: InspectionProfile, *, photo_context: PhotoContext | str = PhotoContext.UNCERTAIN) -> list[CheckResult]:
    p = profile.validate()
    context = PhotoContext.parse(photo_context)
    scope_problem = None
    if context != PhotoContext.ISOLATED_PRODUCT:
        scope_problem = (
            f"Photo context is {context.value.replace('_', ' ')}. Product-only bounds are not established; "
            "the foreground may include a person or scene. Product-dependent checks require manual review."
        )
    h, w = measured.rgb.shape[:2]
    short, long = sorted((w, h))
    seg, q, bg, geometry = measured.segmentation, measured.quality, measured.background, measured.geometry
    checks = []

    def add(identifier, group, status, values, threshold, explanation, evidence=None, reason_code=None):
        code = (reason_code or "near_threshold") if status == Status.REVIEW else None
        checks.append(CheckResult(identifier, group, status, values, threshold, explanation, evidence, code))

    enough_resolution = short >= p.min_short_side and long >= p.min_long_side
    add("low_resolution", "quality", Status.PASS if enough_resolution else Status.FAIL,
        {"short_side": short, "long_side": long}, {"minimum_short_side": p.min_short_side, "minimum_long_side": p.min_long_side},
        f"Native image is {w} × {h} px; requires short side ≥{p.min_short_side} and long side ≥{p.min_long_side} px.", "original")
    quality_problem = scope_problem
    quality_code = "photo_context" if scope_problem else None
    if not quality_problem and not seg.reliable:
        quality_problem = "Product separation is uncertain: " + " ".join(seg.reasons)
        quality_code = "segmentation"
    elif not quality_problem and q["usable_pixels"] < 32:
        quality_problem = "Too few product-interior pixels for a stable quality measurement."
        quality_code = "insufficient_product_pixels"
    elif not quality_problem and (not p.calibration_short_min <= short <= p.calibration_short_max or not p.calibration_long_min <= long <= p.calibration_long_max):
        quality_problem = f"Native size {w} x {h} px is outside this profile's quality support (short side {p.calibration_short_min}-{p.calibration_short_max} px; long side {p.calibration_long_min}-{p.calibration_long_max} px). Analysis is {measured.analysis.shape[1]} x {measured.analysis.shape[0]} px. Quality calibration at this native size is unavailable; this is not a measured defect. Resolution and applicable compliance checks remain independent."
        quality_code = "unsupported_dimensions"
    specs = [
        ("blur", "sharpness" if p.sharpness_metric == "laplacian_variance" else "laplacian_sobel_ratio", p.min_sharpness, True,
         "Laplacian variance" if p.sharpness_metric == "laplacian_variance" else "Laplacian variance / Sobel energy", "higher is sharper"),
        ("low_contrast", "contrast", p.min_contrast, True, "Product luminance P95−P5", "higher means more contrast"),
        ("noise", "noise", p.max_noise, False, "Flat-patch noise sigma", "lower means less estimated noise"),
    ]
    for identifier, key, threshold, minimum, title, meaning in specs:
        score = q.get(key)
        reason = quality_problem
        reason_code = quality_code
        if score is None and not reason:
            reason = "Insufficient flat product patches to estimate noise without confusing texture." if identifier == "noise" else "The selected quality measurement has insufficient signal."
            reason_code = "insufficient_flat_patches" if identifier == "noise" else "insufficient_quality_signal"
        if identifier == "blur" and q.get("contrast", 0) < 15 and not reason:
            reason = "Too little contrast to separate defocus from naturally weak edges."
            reason_code = "blur_contrast_confounded"
        blur_noise_limit = p.max_noise if p.max_blur_noise is None else p.max_blur_noise
        if identifier == "blur" and q.get("noise", 0) > blur_noise_limit and not reason:
            reason = "Noise can inflate sharpness; blur needs manual review."
            reason_code = "blur_noise_confounded"
        if identifier == "blur" and q.get("noise") is None and not reason:
            reason = "Noise cannot be estimated from enough flat product pixels; sharpness may be noise-inflated."
            reason_code = "blur_noise_unavailable"
        if identifier in p.quality_review_reasons and not reason:
            reason, reason_code = p.quality_review_reasons[identifier], "unsupported_quality_calibration"
        band = p.quality_review_bands.get(identifier, p.review_band)
        status = Status.REVIEW if reason else _threshold_status(score, threshold, minimum, band)
        if status == Status.PASS and identifier in p.quality_pass_review_reasons:
            status, reason, reason_code = Status.REVIEW, p.quality_pass_review_reasons[identifier], "unvalidated_pass_region"
        values = {"score": score, "usable_pixels": q["usable_pixels"]}
        if identifier == "blur":
            values.update(metric=p.sharpness_metric, laplacian_variance=q.get("sharpness"), sobel_energy=q.get("sobel_energy"),
                          analysis_width=measured.analysis.shape[1], analysis_height=measured.analysis.shape[0])
        if identifier == "noise":
            values["flat_pixels"] = q.get("noise_pixels", 0)
        limits = {"limit": threshold, "minimum": minimum, "review_band": band}
        if identifier == "blur":
            limits["noise_guard_limit"] = blur_noise_limit
        add(identifier, "quality", status, values, limits,
            reason or f"{title}: {score:.2f}; {meaning}." + (" Near threshold; review required." if status == Status.REVIEW else ""), "mask", reason_code)
    for identifier, key, threshold, median_limit, dark in [
        ("underexposure", "dark_fraction", p.max_dark_fraction, p.min_exposure_luminance, True),
        ("overexposure", "bright_fraction", p.max_bright_fraction, p.max_exposure_luminance, False),
    ]:
        fraction, median = q.get(key), q.get("median_luminance")
        reason = quality_problem or p.quality_review_reasons.get(identifier)
        band = p.quality_review_bands.get(identifier, p.review_band)
        if reason or fraction is None:
            status = Status.REVIEW
        else:
            affected = median < median_limit if dark else median > median_limit
            status = _threshold_status(fraction, threshold, False, band) if affected else Status.PASS
        pass_guard = status == Status.PASS and identifier in p.quality_pass_review_reasons
        if pass_guard:
            status, reason = Status.REVIEW, p.quality_pass_review_reasons[identifier]
        add(identifier, "quality", status, {"score": fraction, "median_luminance": median},
            {"maximum_fraction": threshold, "median_limit": median_limit, "review_band": band},
            reason or (f"Product {'dark' if dark else 'clipped bright'} fraction: {fraction:.1%}; median luminance {median:.1f}. Natural product color can resemble exposure problems." if fraction is not None else "Exposure measurement unavailable."), "mask", quality_code or ("unvalidated_pass_region" if pass_guard else "unsupported_quality_calibration" if reason else "missing_exposure" if fraction is None else None))
    compliance_problem = scope_problem or ("Product/background separation is uncertain: " + " ".join(seg.reasons) if not seg.reliable else None)
    compliance_code = "photo_context" if scope_problem else "segmentation" if not seg.reliable else None
    colors = bg.get("rgb")
    white_fraction = float(np.mean((colors.min(axis=1) >= p.white_min_channel) & (np.ptp(colors, axis=1) <= p.white_max_spread))) if colors is not None else None
    color_status = Status.DISABLED if not p.require_white else (Status.REVIEW if compliance_problem or white_fraction is None else _threshold_status(white_fraction, p.min_white_fraction, True, p.review_band / 3))
    add("background_color", "compliance", color_status, {"score": white_fraction, "background_pixels": bg["pixels"]},
        {"minimum_white_fraction": p.min_white_fraction, "minimum_rgb_channel": p.white_min_channel, "maximum_channel_spread": p.white_max_spread},
        "Any uniform background color is permitted by this profile." if not p.require_white else compliance_problem or (f"White background fraction: {white_fraction:.1%}." if white_fraction is not None else "Too few background pixels."), "mask", compliance_code or ("insufficient_background" if white_fraction is None else None))
    distance = bg.get("distance")
    add("background_uniformity", "compliance", Status.REVIEW if compliance_problem or distance is None else _threshold_status(distance, p.max_background_distance, False, p.review_band),
        {"score": distance, "background_pixels": bg["pixels"]}, {"maximum_delta_e_p90": p.max_background_distance},
        compliance_problem or (f"Background color spread (P90 ΔE76): {distance:.2f}." if distance is not None else "Too few background pixels."), "mask", compliance_code or ("insufficient_background" if distance is None else None))
    tolerance = 1 / min(w, h)
    for identifier, key, lower, upper in [
        ("frame_coverage", "coverage", p.min_coverage, p.max_coverage),
        ("placement", "center_offset", None, p.max_center_offset),
        ("margins", "margin_min", p.min_margin, None),
    ]:
        value = geometry.get(key)
        if compliance_problem or value is None:
            status = Status.REVIEW
        elif any(abs(value - boundary) <= tolerance for boundary in (lower, upper) if boundary is not None):
            status = Status.REVIEW
        else:
            status = Status.FAIL if (lower is not None and value < lower) or (upper is not None and value > upper) else Status.PASS
        add(identifier, "compliance", status, {"score": value, **({"all_margins": geometry["margins"]} if identifier == "margins" and geometry else {})},
            {"minimum": lower, "maximum": upper, "one_pixel_tolerance": tolerance},
            compliance_problem or (f"{identifier.replace('_', ' ').capitalize()}: {value:.1%}." + (" Within one native pixel of a rule boundary." if status == Status.REVIEW else "") if value is not None else "Product bounds unavailable."), "overlay", compliance_code or ("bounds_unavailable" if value is None else "geometry_boundary"))
    contact = geometry.get("edge_pixels")
    add("possible_cropping", "compliance", Status.REVIEW if compliance_problem or contact is None or contact > 0 else Status.PASS,
        {"score": contact}, {"maximum_edge_contact": 0, "advisory_only": True},
        compliance_problem or ("Product touches the frame; possible cropping requires review. Edge contact does not prove missing content." if contact else "No product contact with image boundary detected."), "overlay", compliance_code or ("bounds_unavailable" if contact is None else "cropping_advisory"))
    return checks


def inspect_image(image_bytes: bytes, profile: InspectionProfile | None = None, *, photo_context: PhotoContext | str = PhotoContext.UNCERTAIN) -> InspectionResult:
    started = time.perf_counter()
    profile = (profile or InspectionProfile()).validate()
    context = PhotoContext.parse(photo_context)
    measured = measure_image(image_bytes)
    checks = classify(measured, profile, photo_context=context)
    h, w = measured.rgb.shape[:2]
    mask = cv2.resize(measured.segmentation.mask.astype(np.uint8) * 255, (w, h), interpolation=cv2.INTER_NEAREST)
    overlay = measured.rgb.copy()
    foreground = mask > 0
    overlay[foreground] = (0.65 * overlay[foreground] + 0.35 * np.array([0, 180, 160])).astype(np.uint8)
    overlay_image = Image.fromarray(overlay)
    if measured.geometry:
        x, y, bw, bh = measured.geometry["bbox"]
        ImageDraw.Draw(overlay_image).rectangle((x, y, min(w - 1, x + bw - 1), min(h - 1, y + bh - 1)), outline=(230, 70, 30), width=max(1, round(max(w, h) / 200)))
    notes = list(measured.notes)
    notes.append("Demonstration rules only; these are not official marketplace requirements.")
    if context != PhotoContext.ISOLATED_PRODUCT:
        notes.append("Product-only photo context is not confirmed. The mask and box show estimated foreground, not verified product bounds.")
    if not profile.calibrated:
        notes.append("Quality thresholds are uncalibrated demonstration cutoffs. Consult the development comparison for measured limitations.")
    else:
        notes.append(f"Development-calibrated profile {profile.calibration_id}; consult its held-out evaluation report.")
    original_png, mask_png, overlay_png = png_bytes(measured.rgb), png_bytes(mask), png_bytes(np.array(overlay_image))
    return InspectionResult(
        str(uuid4()), datetime.now(timezone.utc).isoformat(), hashlib.sha256(image_bytes).hexdigest(),
        w, h, PIPELINE_VERSION, profile.to_dict(), context, checks, notes,
        {**measured.segmentation.diagnostics,
         "reliable": measured.segmentation.reliable and context == PhotoContext.ISOLATED_PRODUCT,
         "foreground_mask_reliable": measured.segmentation.reliable,
         "product_scope_confirmed": context == PhotoContext.ISOLATED_PRODUCT,
         "reasons": ([f"Photo context is {context.value}; product-only scope is not confirmed."] if context != PhotoContext.ISOLATED_PRODUCT else []) + measured.segmentation.reasons,
         "bbox": measured.geometry.get("bbox")},
        round((time.perf_counter() - started) * 1000, 2), original_png, mask_png, overlay_png,
    )
