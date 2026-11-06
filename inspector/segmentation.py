from dataclasses import dataclass, field
import threading

import cv2
import numpy as np

GRABCUT_LOCK = threading.Lock()


@dataclass
class Segmentation:
    mask: np.ndarray
    raw_mask: np.ndarray
    bbox: tuple[int, int, int, int] | None
    reliable: bool
    reasons: list[str]
    diagnostics: dict
    stages: dict[str, np.ndarray] = field(default_factory=dict, repr=False)


def lab_image(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)


def segment(rgb: np.ndarray) -> Segmentation:
    h, w = rgb.shape[:2]
    empty = np.zeros((h, w), dtype=bool)
    if min(h, w) < 12:
        return Segmentation(empty, empty, None, False, ["Image is too small for stable segmentation."], {})
    lab = lab_image(rgb)
    border = np.zeros((h, w), bool)
    edge = max(1, round(min(h, w) * 0.04))
    border[:edge] = border[-edge:] = True
    border[:, :edge] = border[:, -edge:] = True
    samples = lab[border]
    background = np.median(samples, axis=0)
    distances = np.linalg.norm(lab - background, axis=2)
    border_distance = distances[border]
    median = float(np.median(border_distance))
    spread = float(np.median(np.abs(border_distance - median)))
    cutoff = max(10.0, min(32.0, median + 3.0 * spread))
    raw = distances > cutoff
    kernel = np.ones((3, 3), np.uint8)
    # Closing repairs small gaps. Component filtering avoids eroding narrow straps and watch bands.
    cleaned = cv2.morphologyEx(raw.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned, 8)
    minimum_component = max(3, int(h * w * 0.001))
    keep = [index for index in range(1, count) if stats[index, cv2.CC_STAT_AREA] >= minimum_component]
    mask = np.isin(labels, keep)
    candidate = mask.copy()
    foreground_fraction = float(mask.mean())
    # A seeded mask permits edge contact; the entire border is never forced to background.
    if 0.01 < foreground_fraction < 0.95 and np.count_nonzero(distances < cutoff * 0.5) >= 10:
        seeds = np.where(mask, cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype(np.uint8)
        seeds[(distances < cutoff * 0.5) & ~mask] = cv2.GC_BGD
        interior = cv2.erode(mask.astype(np.uint8), kernel).astype(bool)
        seeds[interior & (distances > cutoff * 1.5)] = cv2.GC_FGD
        try:
            with GRABCUT_LOCK:
                cv2.setRNGSeed(42)
                cv2.grabCut(rgb, seeds, None, np.zeros((1, 65)), np.zeros((1, 65)), 2, cv2.GC_INIT_WITH_MASK)
            refined = (seeds == cv2.GC_FGD) | (seeds == cv2.GC_PR_FGD)
            if refined.any() and not refined.all():
                mask = refined
        except cv2.error:
            pass
    # Preserve raw edge candidates: cleanup must not conceal a possibly clipped product.
    mask[0] |= raw[0]
    mask[-1] |= raw[-1]
    mask[:, 0] |= raw[:, 0]
    mask[:, -1] |= raw[:, -1]
    lower = distances > cutoff * 0.9
    upper = distances > cutoff * 1.1
    union = np.count_nonzero(lower | upper)
    stability = float(np.count_nonzero(lower & upper) / union) if union else 0.0
    fg_fraction = float(mask.mean())
    boundary_uniform_fraction = float(np.mean(border_distance < 10.0))
    agreement_union = np.count_nonzero(mask | candidate)
    agreement = float(np.count_nonzero(mask & candidate) / agreement_union) if agreement_union else 0.0
    reasons = []
    if fg_fraction < 0.01 or fg_fraction > 0.95:
        reasons.append("Foreground is empty, tiny, or fills almost the entire frame.")
    if np.count_nonzero(~mask) < max(20, h * w * 0.03):
        reasons.append("Too few background pixels remain for assessment.")
    if stability < 0.8 or agreement < 0.65:
        reasons.append("Product mask changes substantially under a small threshold or refinement change.")
    if boundary_uniform_fraction < 0.6:
        reasons.append("Border colors do not establish a reliable plain background.")
    bbox = None
    if mask.any():
        x, y, bw, bh = cv2.boundingRect(mask.astype(np.uint8))
        bbox = (int(x), int(y), int(bw), int(bh))
    return Segmentation(mask, raw, bbox, not reasons, reasons, {
        "foreground_fraction": fg_fraction, "threshold_delta_e": cutoff,
        "stability_iou": stability, "refinement_iou": agreement,
        "uniform_border_fraction": boundary_uniform_fraction,
        "bbox_analysis": bbox, "analysis_width": w, "analysis_height": h,
    }, {"color_distance": distances, "closed": cleaned.astype(bool), "components": candidate})
