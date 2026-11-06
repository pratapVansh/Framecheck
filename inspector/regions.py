"""Independent whole-photo and optional shirt-interior DIP diagnostics, without verdicts."""
import cv2
import numpy as np


def analysis_image(rgb):
    height, width = rgb.shape[:2]
    scale = min(1.0, 512 / max(height, width))
    if scale == 1:
        return rgb
    return cv2.resize(rgb, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)


def region_measurements(rgb, shirt_mask, *, include_product=False):
    """Show scope separation; these raw measurements do not assign quality verdicts."""
    if shirt_mask is not None and shirt_mask.shape != rgb.shape[:2]:
        raise ValueError("Shirt mask must match the original image dimensions.")
    analysis = analysis_image(rgb)
    gray = cv2.cvtColor(analysis, cv2.COLOR_RGB2GRAY).astype(np.float32)
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    gx, gy = cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    support = np.zeros(gray.shape, bool)
    support[1:-1, 1:-1] = True

    def statistics(region):
        pixels = gray[region]
        if pixels.size < 32:
            return {"usable_pixels": int(pixels.size), "reason": "insufficient_pixels"}
        low, high = np.percentile(pixels, [5, 95])
        energy = float(np.mean((gx * gx + gy * gy)[region]))
        sharpness = float(np.var(laplacian[region]))
        return {"usable_pixels": int(pixels.size), "contrast_p95_p5": float(high - low),
                "median_luminance": float(np.median(pixels)), "laplacian_variance": sharpness,
                "laplacian_sobel_ratio": sharpness / energy if energy > 1e-8 else None}

    product = None
    if include_product and shirt_mask is not None:
        resized = cv2.resize(shirt_mask.astype(np.uint8), gray.shape[::-1], interpolation=cv2.INTER_NEAREST)
        interior = cv2.erode(resized, np.ones((3, 3), np.uint8)).astype(bool)
        product = statistics(interior & support)
    return {"native_width": rgb.shape[1], "native_height": rgb.shape[0],
            "whole_photo": statistics(support), "product": product,
            "product_reason": None if product is not None else "shirt_mask_not_verified",
            "verdict": None, "calibration": "No whole-photo or neural-mask profile has been validated."}


