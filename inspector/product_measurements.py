"""DIP measurements for explicit scopes; preserve the frozen legacy measurement code."""
import cv2
import numpy as np

from inspector.pipeline import Measurements
from inspector.regions import analysis_image
from inspector.segmentation import Segmentation, lab_image


def frame_geometry(bbox, width, height):
    """Measure a product group's bounds in original frame coordinates."""
    x, y, bw, bh = bbox
    margins = [x / width, y / height, (width - x - bw) / width, (height - y - bh) / height]
    return {'bbox': list(bbox), 'coverage': max(bw / width, bh / height),
            'center_offset': max(abs((x + bw / 2) / width - .5), abs((y + bh / 2) / height - .5)),
            'margins': margins, 'margin_min': min(margins)}


def measure_scope(rgb, native_mask, *, reliable=True, reason=""):
    h, w = rgb.shape[:2]
    analysis = analysis_image(rgb)
    mask = cv2.resize(native_mask.astype(np.uint8), analysis.shape[1::-1], interpolation=cv2.INTER_NEAREST).astype(bool)
    bbox = tuple(int(v) for v in cv2.boundingRect(mask.astype(np.uint8))) if mask.any() else None
    seg = Segmentation(mask, mask.copy(), bbox, reliable, [reason] if reason else [], {})
    gray = cv2.cvtColor(analysis, cv2.COLOR_RGB2GRAY).astype(np.float32)
    interior = cv2.erode(seg.mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    valid = interior & (np.indices(gray.shape)[0] > 0) & (np.indices(gray.shape)[0] < gray.shape[0] - 1) & (np.indices(gray.shape)[1] > 0) & (np.indices(gray.shape)[1] < gray.shape[1] - 1)
    pixels = gray[valid]
    q = {"usable_pixels": int(pixels.size)}
    if pixels.size >= 32:
        low, high = np.percentile(pixels, [5, 95])
        q.update(contrast=float(high - low), luminance_p05=float(low), luminance_p95=float(high),
                 median_luminance=float(np.median(pixels)),
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
    return Measurements(rgb, analysis, seg, q, background, geometry, [])
