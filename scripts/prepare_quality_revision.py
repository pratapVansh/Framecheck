"""Prepare small-photo development data and unseen source groups before calibration."""
from collections import Counter
from copy import deepcopy
import csv
import hashlib
import json
from pathlib import Path
import random

import cv2
import numpy as np
from PIL import Image, ImageDraw

from inspector.benchmark import image_bytes, read_manifest, write_manifest
from inspector.images import decode_image, png_bytes
from inspector.models import COMPLIANCE
from scripts.dataset import category_of, fingerprint

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/quality-v3"
REPORT = ROOT / "reports/quality-v3"


def padded(rgb):
    h, w = rgb.shape[:2]
    assert h <= 96 and w <= 96
    result = np.full((96, 96, 3), 255, np.uint8)
    y, x = (96 - h) // 2, (96 - w) // 2
    result[y:y+h, x:x+w] = rgb
    return result


def main():
    assert not (DATA / "development.jsonl").exists(), "Refusing to replace prepared data"
    existing = read_manifest(ROOT / "data/revision-v2/benchmark-v2.2.jsonl")
    development = [r for r in existing if r["split"] in {"train", "validation"}]
    output = DATA / "padded-development"
    output.mkdir(exist_ok=False)
    added = []
    for row in development:
        rgb, _ = decode_image(image_bytes(row))
        if max(rgb.shape[:2]) > 96 or min(rgb.shape[:2]) < 48:
            continue
        target = output / (hashlib.sha256(row["id"].encode()).hexdigest()[:20] + ".png")
        target.write_bytes(png_bytes(padded(rgb)))
        clone = deepcopy(row)
        clone.update(id=row["id"] + "__canvas96", image=str(target), image_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                     width=96, height=96, kind="control", mask=None, mask_reviewed=False,
                     review_method="inherited_AI_quality_labels_exact_pixel_padding", human_verified=False,
                     transforms=[{"method": "white_canvas_96_no_pixel_resampling", "source": row["id"]}])
        clone["labels"].update({key: None for key in (*COMPLIANCE, "low_resolution")})
        clone["review_notes"] = "Product pixels and quality labels inherited unchanged; geometry/background/resolution truth is not inherited. Padding is not new native detail."
        added.append(clone)
    write_manifest(DATA / "development.jsonl", development + added)
    old_sources = read_manifest(ROOT / "data/manifest.jsonl") + [r for r in existing if r["kind"] == "original"]
    used_ids = {r["source_id"] for r in old_sources}
    used_hashes = {hashlib.sha256(image_bytes(r)).hexdigest() for r in old_sources}
    used_pixels = set()
    perceptual = []
    for row in old_sources:
        rgb, _ = decode_image(image_bytes(row))
        used_pixels.add(hashlib.sha256(rgb.tobytes()).hexdigest())
        perceptual.append(fingerprint(rgb))
    buckets = {category: [] for category in ("apparel", "footwear", "bags", "watches")}
    with (ROOT / "data/raw/archive/myntradataset/styles.csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        for cells in reader:
            if len(cells) != len(header):
                continue
            row = dict(zip(header, cells)); category = category_of(row)
            if category and row["id"] not in used_ids:
                buckets[category].append(row)
    rng = random.Random(20261105)
    selected = []
    for category, candidates in buckets.items():
        rng.shuffle(candidates)
        accepted = 0
        for metadata in candidates:
            path = ROOT / "data/raw/archive/myntradataset/images" / (metadata["id"] + ".jpg")
            if not path.exists():
                continue
            blob = path.read_bytes(); rgb, _ = decode_image(blob)
            encoded, pixels = hashlib.sha256(blob).hexdigest(), hashlib.sha256(rgb.tobytes()).hexdigest()
            phash = fingerprint(rgb)
            if encoded in used_hashes or pixels in used_pixels or any((phash ^ old).bit_count() <= 4 for old in perceptual):
                continue
            perceptual.append(phash); used_hashes.add(encoded); used_pixels.add(pixels)
            selected.append({"id": metadata["id"], "image": str(path), "category": category, "image_sha256": encoded,
                             "decoded_sha256": pixels, "selection": "seeded metadata sampling; excludes all prior sources, exact pixels, and pHash distance <=4",
                             "metadata": metadata, "split": "test", "reviewed": False})
            accepted += 1
            if accepted == 8:
                break
        assert accepted == 8
    (DATA / "test-sources.draft.json").write_text(json.dumps(selected, indent=2), encoding="utf-8")
    for start in range(0, len(selected), 16):
        sheet = Image.new("RGB", (960, 760), "#e9edf1")
        draw = ImageDraw.Draw(sheet)
        for i, row in enumerate(selected[start:start+16]):
            x, y = (i % 8) * 120, (i // 8) * 380
            with Image.open(row["image"]) as image:
                view = image.convert("RGB").resize((120, 160), Image.Resampling.NEAREST)
            sheet.paste(view, (x, y+35)); draw.text((x+3, y+5), row["id"] + " " + row["category"], fill="black")
            # Same-size blurred evidence is reviewed before any test predictions.
            rgb, _ = decode_image(Path(row["image"]).read_bytes())
            blurred = cv2.GaussianBlur(rgb, (5, 5), 1.0)
            sheet.paste(Image.fromarray(blurred).resize((120,160), Image.Resampling.NEAREST), (x, y+210))
            draw.text((x+3, y+195), "Gaussian sigma 1", fill="black")
        sheet.save(REPORT / f"test-label-review-{start//16+1}.png")
    report = {"development_counts": dict(Counter(r["split"] for r in development+added)),
              "fresh_test_source_groups": len(selected), "fresh_test_predictions_run": False,
              "regression_sources_excluded": True, "normal_photo_calibration_available": False,
              "limitations": "Only 60x80 source photos and exact-pixel 96x96 padding controls. External download blocked; do not infer normal-photo support."}
    (REPORT / "data-preparation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
