"""Offline shirt-mask pilot. Never changes production inspection or calibration."""
import argparse
from collections import Counter
import hashlib
import html
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

from inspector.images import decode_image, png_bytes
from inspector.garments import load_shirt_model, prepare_segformer_input
from inspector.regions import analysis_image, region_measurements
from inspector.segmentation import segment

MODEL_ID = "mattmdjaga/segformer_b2_clothes"
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mask_metrics(predicted, reference):
    if predicted.shape != reference.shape:
        raise ValueError("Prediction and reference must use the same pixel coordinates.")
    if not reference.any():
        raise ValueError("A shirt reference must contain foreground pixels.")
    intersection = int(np.count_nonzero(predicted & reference))
    predicted_pixels = int(predicted.sum())
    reference_pixels = int(reference.sum())
    return {
        "iou": intersection / int(np.count_nonzero(predicted | reference)),
        "missed_reference_fraction": 1 - intersection / reference_pixels,
        "outside_reference_fraction": (predicted_pixels - intersection) / predicted_pixels if predicted_pixels else None,
    }


def scoped_dip(rgb, shirt_mask, *, mask_reliable=False):
    return region_measurements(rgb, shirt_mask, include_product=mask_reliable)


def hardware():
    result = {}
    for name, args in {
        "nvidia_smi": ["nvidia-smi"],
        "gpu_details": ["nvidia-smi", "--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader"],
        "nvcc": ["nvcc", "--version"],
    }.items():
        try:
            command = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
            result[name] = {"exit_code": command.returncode, "stdout": command.stdout, "stderr": command.stderr}
        except (OSError, subprocess.TimeoutExpired) as exc:
            result[name] = {"error": str(exc)}
    for name in ("torch", "transformers", "safetensors"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    try:
        import torch
        result.update(torch_build=torch.__version__, torch_cuda_build=torch.version.cuda,
                      torch_cuda_available=torch.cuda.is_available(), torch_cuda_devices=torch.cuda.device_count())
    except ImportError:
        result["torch_cuda_available"] = False
    return result


def overlay(rgb, mask):
    output = rgb.copy()
    output[mask] = (rgb[mask] * 0.55 + np.array([0, 190, 160]) * 0.45).astype(np.uint8)
    return output


def timing(values):
    return {"samples": len(values), "median_ms": float(np.median(values)), "p95_ms": float(np.percentile(values, 95))} if values else None


def reference_kind(row):
    if row.get("reference_human_verified") is True:
        return "human_verified"
    return "ai_reviewed" if re.search(r"\bAI\b", str(row.get("reference_provenance", "")), re.IGNORECASE) else "unverified"


def reference_summary(records):
    groups = {}
    for kind in ("human_verified", "ai_reviewed", "unverified"):
        rows = [r for r in records if r.get("reference_mask") and reference_kind(r) == kind]
        groups[kind] = {"reference_photos": len(rows)}
        for method in ("baseline", "pretrained"):
            values = [r[method + "_reference_metrics"]["iou"] for r in rows if r.get(method + "_reference_metrics") is not None]
            groups[kind][method] = {"compared_photos": len(values), "mean_iou": float(np.mean(values)) if values else None}
    return groups


def run(manifest, model_dir, output):
    rows = [json.loads(line) for line in Path(manifest).read_text(encoding="utf-8").splitlines() if line.strip()]
    if not 1 <= len(rows) <= 50 or len({r['id'] for r in rows}) != len(rows):
        raise ValueError("Pilot requires 1-50 uniquely identified photos.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    protected = list((ROOT / "inspector").glob("*.py")) + [ROOT / "data/quality-v3-profile.json"]
    before = {str(p): digest(p) for p in protected}
    hardware_result = hardware()
    predict, model_info = load_shirt_model(model_dir)
    results, baseline_times, pretrained_times = [], [], []
    first, _ = decode_image(Path(rows[0]["image"]).read_bytes())
    segment(analysis_image(first))
    if predict:
        started = time.perf_counter()
        predict(first)
        model_info["warmup_ms"] = (time.perf_counter() - started) * 1000
    for index, row in enumerate(rows):
        source = Path(row["image"])
        if digest(source) != row["image_sha256"]:
            raise ValueError("Pilot source changed: " + row["id"])
        rgb, _ = decode_image(source.read_bytes())
        analysis = analysis_image(rgb)
        measured_times = []
        for _ in range(3):
            started = time.perf_counter()
            baseline = segment(analysis)
            measured_times.append((time.perf_counter() - started) * 1000)
        baseline_times.extend(measured_times)
        baseline_mask = cv2.resize(baseline.mask.astype(np.uint8), rgb.shape[1::-1], interpolation=cv2.INTER_NEAREST).astype(bool)
        neural_mask = None
        neural_times = []
        if predict:
            for _ in range(3):
                started = time.perf_counter()
                neural_mask = predict(rgb)
                neural_times.append((time.perf_counter() - started) * 1000)
            pretrained_times.extend(neural_times)
        reference = None
        if row.get("reference_mask"):
            if digest(row["reference_mask"]) != row["reference_sha256"]:
                raise ValueError("Pilot reference changed: " + row["id"])
            with Image.open(row["reference_mask"]) as image:
                reference = np.array(image.convert("L")) > 127
        folder = output / row["id"]
        folder.mkdir()
        assets = {"original": rgb, "baseline-mask": baseline_mask * 255, "baseline-overlay": overlay(rgb, baseline_mask)}
        if neural_mask is not None:
            assets.update({"pretrained-mask": neural_mask * 255, "pretrained-overlay": overlay(rgb, neural_mask)})
        if reference is not None:
            assets["reference-mask"] = reference * 255
        for name, array in assets.items():
            (folder / (name + ".png")).write_bytes(png_bytes(array))
        record = {**row, "native_size": [rgb.shape[1], rgb.shape[0]],
                  "baseline_ms": measured_times, "pretrained_ms": neural_times or None,
                  "baseline_heuristic_reliable": baseline.reliable,
                  "baseline_product_reliable": False,
                  "pretrained_product_reliable": False if neural_mask is not None else None,
                  "baseline_reference_metrics": mask_metrics(baseline_mask, reference) if reference is not None else None,
                  "pretrained_reference_metrics": mask_metrics(neural_mask, reference) if reference is not None and neural_mask is not None else None,
                  "scoped_dip_demo": scoped_dip(rgb, neural_mask, mask_reliable=False),
                  "wrong_item_selection": {"baseline": None, "pretrained": None},
                  "review_status": "Needs independent human review; missing model results are null, not zero."}
        results.append(record)
        print(f"{index + 1}/{len(rows)} {row['id']}: baseline ready; pretrained {'ready' if predict else 'blocked'}", flush=True)
    summary = {"status": "comparison_complete_unverified" if predict else "blocked_pretrained_comparison",
               "model": model_info, "hardware": hardware_result, "manifest_sha256": digest(manifest),
               "photos": len(rows), "contexts": dict(Counter(r["context"] for r in rows)),
               "minor_occlusion_photos": sum(r["occlusion"] == "minor_visible_overlap" for r in rows),
               "multiple_garment_photos": sum(r["multiple_garments"] for r in rows),
               "multiple_target_shirt_photos": sum(r["multiple_target_shirts"] for r in rows),
               "AI_reference_masks": sum(bool(r.get("reference_mask")) and reference_kind(r) == "ai_reviewed" for r in rows),
               "human_verified_masks": sum(bool(r.get("reference_mask")) and reference_kind(r) == "human_verified" for r in rows),
               "reference_metrics_by_provenance": reference_summary(results),
               "baseline_timing": timing(baseline_times), "pretrained_timing": timing(pretrained_times),
               "timing_policy": "Three warm runs per photo; decode/save excluded. Neural preprocessing, forward and native-mask postprocessing included. Initialization/warmup separate.",
               "opencv_threads": cv2.getNumThreads(),
               "native_size_counts": dict(Counter(f"{r['native_size'][0]}x{r['native_size'][1]}" for r in results)),
               "limitations": ["Available photo sizes are reported explicitly; source-camera originals and representative normal-size coverage are not established.",
                               "Context coverage is limited to the supplied source records and visual review; do not infer unsupported contexts.",
                               "AI masks are diagnostic references, not independent verified truth.",
                               "No neural mask or whole-photo DIP calibration has been established.",
                               "No production inspector, profile, saved history, or GenAI integration is changed."],
               "protected_sha256": before, "records": results}
    if any(digest(path) != value for path, value in before.items()):
        raise RuntimeError("Protected production file changed during the pilot.")
    (output / "results.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    render(rows, output, predict is not None)
    print(json.dumps({k: v for k, v in summary.items() if k in {"status", "photos", "baseline_timing", "pretrained_timing"}}))
    return 0 if predict else 2


def render(rows, output, has_model):
    cards = []
    kinds = ["original", "baseline-mask", "pretrained-mask", "baseline-overlay", "pretrained-overlay", "reference-mask"]
    for page in range((len(rows) + 4) // 5):
        subset = rows[page * 5:page * 5 + 5]
        sheet = Image.new("RGB", (220 * len(kinds), len(subset) * 250), "#eeeeee")
        draw = ImageDraw.Draw(sheet)
        for index, row in enumerate(subset):
            y = index * 250
            draw.text((5, y + 3), f"{row['id']} | {row['context']} | target: {row['target']}", fill="black")
            pictures = []
            for column, kind in enumerate(kinds):
                source = output / row["id"] / (kind + ".png")
                label = reference_kind(row).replace("_", " ") + " reference" if kind == "reference-mask" else kind
                draw.text((column * 220 + 5, y + 24), label, fill="black")
                if source.exists():
                    with Image.open(source) as image:
                        image = image.convert("RGB")
                        scale = min(205 / image.width, 195 / image.height)
                        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.NEAREST)
                        sheet.paste(image, (column * 220 + (220 - image.width) // 2, y + 45))
                    rel = f"{row['id']}/{kind}.png"
                    pictures.append(f'<figure><img src="{rel}" alt="{label}"><figcaption>{label}</figcaption></figure>')
                else:
                    reason = "NOT RUN: checkpoint missing" if kind.startswith("pretrained-") else "NO REFERENCE MASK"
                    draw.text((column * 220 + 5, y + 100), reason, fill="#9b2121")
                    pictures.append(f'<figure><p>{reason}</p><figcaption>{label}</figcaption></figure>')
            title = html.escape(f"{row['id']} | {row['context']} | {row['target']}")
            notes = html.escape(" ".join(row["notes"]))
            cards.append(f'<article><h2>{title}</h2><p>{notes}</p><div>{"".join(pictures)}</div></article>')
        sheet.save(output / f"review-sheet-{page + 1}.png")
    status = "Model predictions available; human validation still missing." if has_model else "Pretrained comparison blocked: weights unavailable. Baseline results only."
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Framecheck garment pilot</title>
<style>body{{font:16px system-ui;margin:30px;max-width:1200px}}article{{border-top:1px solid #ccc;padding:12px 0}}article div{{display:flex;gap:16px;flex-wrap:wrap}}figure{{width:220px;margin:0}}img{{width:150px;height:200px;object-fit:contain;image-rendering:pixelated}}figcaption{{font-size:13px}}</style>
<h1>Framecheck shirt segmentation pilot</h1><p>{status}</p><p>{len(rows)} available photos. AI annotations are not human-verified. Display scaling adds no detail. No product-level verdicts are authorized.</p>{''.join(cards)}</html>'''
    (output / "comparison.html").write_text(document, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "data/garment-pilot/manifest.jsonl")
    parser.add_argument("--model-dir", type=Path, default=ROOT / "data/garment-pilot/model")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.manifest, args.model_dir, args.output))


if __name__ == "__main__":
    main()
