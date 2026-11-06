"""Seeded synthetic variants of reviewed Fashion Product Images Small originals."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from inspector.benchmark import image_bytes, read_manifest, verify_split_freeze, write_manifest
from inspector.images import decode_image, png_bytes
from inspector.models import DEFECTS, PhotoContext

MASK_REQUIRED = {"background_color", "background_uniformity", "frame_coverage", "placement", "margins", "possible_cropping"}


def transform(rgb, defect, severity, seed, mask=None):
    rng = np.random.default_rng(seed)
    source = rgb.astype(np.float32)
    params = {"defect": defect, "severity": severity, "seed": seed}
    result_mask = mask.copy() if mask is not None else None
    if defect == "blur":
        if seed % 2:
            size = 3 if severity == 1 else 7
            kernel = np.zeros((size, size), np.float32)
            kernel[size // 2] = 1 / size
            output = cv2.filter2D(rgb, -1, kernel)
            params.update(method="motion", kernel=size)
        else:
            sigma = 0.7 if severity == 1 else 1.5
            output = cv2.GaussianBlur(rgb, (0, 0), sigma)
            params.update(method="gaussian", sigma=sigma)
    elif defect == "noise":
        sigma = 8 if severity == 1 else 20
        output = source + rng.normal(0, sigma, source.shape)
        params["sigma"] = sigma
    elif defect == "low_contrast":
        factor = 0.4 if severity == 1 else 0.15
        output = (source - 128) * factor + 128
        params["factor"] = factor
    elif defect == "underexposure":
        factor = 0.3 if severity == 1 else 0.08
        output = source * factor
        params["factor"] = factor
    elif defect == "overexposure":
        factor, offset = (1.7, 65) if severity == 1 else (2.8, 130)
        output = source * factor + offset
        params.update(factor=factor, offset=offset)
    elif defect == "low_resolution":
        short = 45 if severity == 1 else 30
        scale = min(1.0, short / min(rgb.shape[:2]))
        width, height = max(1, round(rgb.shape[1] * scale)), max(1, round(rgb.shape[0] * scale))
        output = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
        if mask is not None:
            result_mask = cv2.resize(mask.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST).astype(bool)
        params.update(width=width, height=height)
    elif defect in MASK_REQUIRED:
        if mask is None or not mask.any() or mask.all():
            raise ValueError("Background and layout variants require a reviewed product mask with recorded provenance.")
        h, w = rgb.shape[:2]
        if defect == "background_color":
            output = rgb.copy()
            color = (208, 220, 238) if severity == 1 else (75, 125, 170)
            output[~mask] = color
            params["color"] = color
        elif defect == "background_uniformity":
            output = rgb.copy()
            yy, xx = np.indices((h, w))
            amplitude = 40 if severity == 1 else 100
            pattern = 255 - amplitude * ((xx // 5 + yy // 5) % 2)
            background = np.repeat(pattern[:, :, None], 3, axis=2)
            output[~mask] = background[~mask]
            params.update(pattern="checker", amplitude=amplitude)
        else:
            x, y, bw, bh = cv2.boundingRect(mask.astype(np.uint8))
            target = 0.42 if severity == 1 else 0.28
            if defect in {"margins", "possible_cropping"}:
                target = 0.80
            elif defect == "placement":
                target = 0.60
            scale = target / max(bw / w, bh / h)
            nw, nh = max(1, round(bw * scale)), max(1, round(bh * scale))
            product = cv2.resize(rgb[y:y + bh, x:x + bw], (nw, nh), interpolation=cv2.INTER_LINEAR)
            product_mask = cv2.resize(mask[y:y + bh, x:x + bw].astype(np.uint8), (nw, nh), interpolation=cv2.INTER_NEAREST).astype(bool)
            left, top = (w - nw) // 2, (h - nh) // 2
            if defect == "placement":
                left = max(0, round((0.5 + (0.17 if severity == 1 else 0.20)) * w - nw / 2))
            elif defect == "margins":
                left = 0 if severity == 2 else 1
            elif defect == "possible_cropping":
                left = -max(1, round(nw * (0.08 if severity == 1 else 0.18)))
            output = np.full_like(rgb, 255)
            result_mask = np.zeros((h, w), bool)
            dx, dy = max(0, left), max(0, top)
            sx, sy = max(0, -left), max(0, -top)
            visible_w, visible_h = min(nw - sx, w - dx), min(nh - sy, h - dy)
            visible_mask = product_mask[sy:sy + visible_h, sx:sx + visible_w]
            output[dy:dy + visible_h, dx:dx + visible_w][visible_mask] = product[sy:sy + visible_h, sx:sx + visible_w][visible_mask]
            result_mask[dy:dy + visible_h, dx:dx + visible_w] = visible_mask
            retained = float(result_mask.sum() / max(1, product_mask.sum()))
            if retained < 0.75:
                raise ValueError("Generated crop retained less than 75% of the product; variant omitted.")
            params.update(target_extent=target, translation=[left, top], retained_product_fraction=retained)
    else:
        raise ValueError(f"Unsupported defect {defect}.")
    if defect in {"noise", "low_contrast", "underexposure", "overexposure", "blur"} and mask is not None:
        # Limit quality changes to the product when independent human masks exist.
        changed = np.clip(output, 0, 255).astype(np.uint8)
        output = rgb.copy()
        output[mask] = changed[mask]
        params["region"] = "reviewed_product_mask"
    return np.clip(output, 0, 255).astype(np.uint8), result_mask, params


def generate(manifest, output, per_split=10, seed=42):
    if output.exists():
        raise ValueError("Output already exists. Preserve reviewed variants and choose a new output.")
    originals = read_manifest(manifest)
    if not originals or any(not row["reviewed"] or row["split"] is None or row.get("kind") != "original" for row in originals):
        raise ValueError("Generate only after reviewing and splitting every original.")
    verify_split_freeze(manifest)
    generated = deepcopy(originals)
    destination = output.parent / (output.stem + "_variants")
    if destination.exists():
        raise ValueError("Variant directory already exists; choose a new output manifest.")
    destination.mkdir(parents=True)
    omissions, counts = [], {}

    def emit(row, defects, severity, number):
        identity = row["id"] + "__" + "+".join(defects) + f"__s{severity}"
        variant_seed = int.from_bytes(hashlib.sha256(f"{seed}:{identity}".encode()).digest()[:4], "big")
        rgb, _ = decode_image(image_bytes(row))
        mask = None
        if row.get("mask") and row.get("mask_reviewed"):
            with Image.open(row["mask"]) as image:
                mask = np.array(image.convert("L")) > 127
            if mask.shape != rgb.shape[:2]:
                raise ValueError("Reviewed mask dimensions do not match source image.")
        parameters = []
        for index, defect in enumerate(defects):
            rgb, mask, details = transform(rgb, defect, severity, variant_seed + index, mask)
            parameters.append(details)
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        image_path = destination / (digest + ".png")
        image_path.write_bytes(png_bytes(rgb))
        item = deepcopy(row)
        item.update(id=identity, image=str(image_path.resolve()), kind="mixed" if len(defects) > 1 else "synthetic" if defects else "control",
                    severity=severity if defects else "control", reviewed=False, mask=None, mask_reviewed=False,
                    transforms=parameters or [{"method": "unchanged_png_reencoding", "seed": variant_seed}],
                    width=rgb.shape[1], height=rgb.shape[0])
        item["photo_context"] = PhotoContext.parse(row.get("photo_context", PhotoContext.UNCERTAIN))
        item["image_sha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
        item["source_annotation_provenance"] = {key: row.get(key) for key in
            ("id", "image_sha256", "review_method", "reviewer", "reviewed_at", "human_verified", "mask_review_method", "mask_human_verified")}
        for key in ("reviewer", "reviewed_at", "review_notes", "review_method", "ai_review", "ai_draft",
                    "ai_mask_draft", "mask_sha256", "mask_reviewer", "mask_reviewed_at", "mask_review_method", "mask_origin"):
            item.pop(key, None)
        item.update(human_verified=False, human_confirmed=False, mask_human_verified=False, mask_human_confirmed=False)
        for defect in defects:
            item["labels"][defect] = True
        item["label_note"] = "Inherited and transformation-suggested labels only. Review all twelve categories and record the actual review method before use."
        if mask is not None:
            mask_path = destination / (digest + "_mask.png")
            mask_path.write_bytes(png_bytes(mask.astype(np.uint8) * 255))
            item["mask"] = str(mask_path.resolve())
            item["mask_sha256"] = hashlib.sha256(mask_path.read_bytes()).hexdigest()
            item["mask_origin"] = "Transformed source mask; inherits its source annotation method, not a new independent review."
        generated.append(item)

    rng = np.random.default_rng(seed)
    for split in ("train", "validation", "test"):
        members = [row for row in originals if row["split"] == split]
        for defect in DEFECTS:
            eligible = [row for row in members if row["labels"][defect] is False and (defect not in MASK_REQUIRED or row.get("mask_reviewed"))]
            rng.shuffle(eligible)
            # Distinct product groups, not merely distinct image IDs, provide category support.
            unique = list({row["group_id"]: row for row in eligible}.values())[:per_split]
            counts[f"{split}:{defect}"] = len(unique)
            for number, row in enumerate(unique):
                for severity in (1, 2):
                    try:
                        emit(row, [defect], severity, number)
                    except ValueError as exc:
                        omissions.append({"source": row["id"], "defect": defect, "reason": str(exc)})
        mixed_pairs = (("blur", "noise"), ("low_contrast", "underexposure"), ("placement", "background_uniformity"))
        for pair in mixed_pairs:
            eligible = [row for row in members if all(row["labels"][defect] is False for defect in pair)
                        and (not (set(pair) & MASK_REQUIRED) or row.get("mask_reviewed"))]
            rng.shuffle(eligible)
            unique = list({row["group_id"]: row for row in eligible}.values())[:per_split]
            for number, row in enumerate(unique):
                emit(row, list(pair), 2, number)
        for number, row in enumerate(members[:per_split]):
            emit(row, [], 0, number)
    write_manifest(output, generated)
    output.with_suffix(".generation.json").write_text(json.dumps({"seed": seed, "source": str(manifest), "variants": len(generated) - len(originals), "eligible_source_counts": counts, "omissions": omissions,
        "source_frozen_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "note": "Generated variants and transformed masks require a new review with explicit provenance. AI review is not human verification. No performance labels are asserted by generation."}, indent=2), encoding="utf-8")
    print(f"Wrote {len(generated) - len(originals)} variants. Review every variant before calibration or evaluation.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("data/benchmark.jsonl"))
    parser.add_argument("--per-split", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        if args.per_split < 1:
            raise ValueError("per-split must be positive.")
        generate(args.manifest, args.output, args.per_split, args.seed)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
