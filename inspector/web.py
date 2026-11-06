"""Local, dependency-free web interface for inspection and dataset review."""
import argparse
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID, uuid4

import numpy as np
from PIL import Image, ImageDraw

from inspector.benchmark import image_bytes, read_manifest, write_manifest
from inspector.images import decode_image, png_bytes
from inspector import PIPELINE_VERSION
from inspector.models import DEFECTS, PHOTO_CONTEXT_LABELS, InspectionProfile, PhotoContext, demo_profiles
from inspector.pipeline import inspect_image
from inspector.shirt_locator import inspect_with_shirt_locator, select_shirt_target
from inspector.product_locator import inspect_product, select_product
from inspector.product_forms import marking_form, whole_photo_panel
from inspector.auto_inspect import inspect_auto, amend_inspection
from inspector import modern_ui
from inspector.narration import explain
from inspector.profiles import load_profile
from inspector.storage import list_inspections, load_inspection, save_inspection

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REVIEW_LOCK = threading.Lock()
MAX_REQUEST = 11 * 1024 * 1024

CSS = """
:root{font:16px/1.5 system-ui,sans-serif;color:#172b3a;background:#f5f8fa;--teal:#087e8b;--line:#dce5eb}
*{box-sizing:border-box}body{margin:0}header{background:#102c3b;color:white;padding:18px max(24px,calc((100vw - 1120px)/2));display:flex;align-items:center;gap:28px}header a{color:#d9e8ee;text-decoration:none;font-size:14px}header .brand{font-weight:750;font-size:18px;color:white;margin-right:auto;white-space:nowrap}.brand span{color:#59d3c4}main{max-width:1120px;margin:38px auto;padding:0 24px 50px}.eyebrow{color:var(--teal);text-transform:uppercase;letter-spacing:.13em;font-size:12px;font-weight:800}h1{font-size:clamp(28px,4vw,40px);line-height:1.15;letter-spacing:-.035em;margin:9px 0 14px}h2{font-size:21px;margin:0 0 16px}h3{font-size:16px;margin:0 0 10px}p{margin:10px 0}.muted,small{color:#5c7180}.intro{max-width:680px;margin-bottom:26px}.grid{display:grid;grid-template-columns:1.35fr 1fr;gap:24px}.card{background:white;border:1px solid var(--line);border-radius:14px;padding:25px;margin-bottom:22px;box-shadow:0 3px 12px #15344705}.upload{border:2px dashed #b6cdd5;border-radius:10px;background:#f7fbfc;min-height:205px;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:25px;gap:14px;text-align:center}.upload b{font-size:18px}.upload input{max-width:100%}input,select,textarea,button{font:inherit}input[type=number],input[type=text],select,textarea{padding:9px 11px;border:1px solid #b9cbd5;border-radius:7px;background:white;color:inherit;width:100%}label{display:block;font-size:13px;font-weight:650;margin-bottom:6px}.field{margin:14px 0}.fields{display:grid;grid-template-columns:1fr 1fr;gap:4px 15px}.button,button{display:inline-block;background:var(--teal);color:white;border:0;border-radius:8px;padding:11px 20px;font-weight:650;text-decoration:none;cursor:pointer}.secondary{background:#e9f1f4;color:#244450}.actions{display:flex;align-items:center;gap:13px;flex-wrap:wrap;margin:20px 0}.notice{border-left:3px solid #d6a347;background:#fff9e9;padding:12px 15px;font-size:14px;margin:18px 0;border-radius:5px}.error{background:#fff0ef;border-color:#c44038}.pill{display:inline-block;border-radius:20px;padding:4px 11px;font-size:12px;font-weight:750;background:#edf1f4;color:#526b7c;white-space:nowrap}.pass{background:#e5f6ee;color:#136442}.fail{background:#ffe9e7;color:#a02d26}.manual-review{background:#fff0cd;color:#805b10}.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin:22px 0}.stat{border:1px solid var(--line);border-radius:10px;background:white;padding:17px}.stat small{display:block;margin-bottom:8px}.evidence{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}.evidence figure{margin:0}.evidence img,.reference{display:block;width:100%;height:250px;object-fit:contain;background:#f5f7f9;border:1px solid var(--line);border-radius:8px;image-rendering:auto}.evidence figcaption{font-size:13px;margin-top:8px;color:#526b7c}.check{padding:16px 0;border-bottom:1px solid #e7edf1}.check:last-child{border:0}.check-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.check p{font-size:14px;color:#526b7c;margin-bottom:0}details{margin-top:14px}summary{cursor:pointer;font-size:13px;color:var(--teal);font-weight:650}pre{overflow:auto;white-space:pre-wrap;font-size:12px;background:#f4f7f9;border-radius:7px;padding:12px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{text-align:left;padding:12px;border-bottom:1px solid var(--line)}a{color:var(--teal)}footer{margin-top:32px;font-size:12px;color:#6b808d}.labels{display:grid;grid-template-columns:1fr 1fr;gap:10px 20px}.labels .field{margin:2px 0}.reference{height:400px;image-rendering:pixelated}.empty{padding:45px 15px;text-align:center;color:#58707e}.caption{font-size:12px;color:#6b808d}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid #e5a73e;outline-offset:3px}@media(max-width:760px){header{padding:16px 20px;gap:14px;flex-wrap:wrap}.grid,.evidence{grid-template-columns:1fr}.stats{grid-template-columns:1fr}.evidence img{height:220px}main{margin-top:25px}td,th{padding:8px}.labels{grid-template-columns:1fr}}
.simple{max-width:720px;margin:0 auto}.simple .card{padding:22px}.simple h1{font-size:30px}.simple .result-photo{display:block;width:auto;max-width:100%;height:auto;max-height:480px;object-fit:contain;background:#f5f7f9;border-radius:8px;margin:0 auto}.simple .evidence img{width:auto;max-width:100%;height:auto;max-height:320px;margin:0 auto}.simple .evidence{grid-template-columns:1fr 1fr}.simple .check summary{font-size:15px;color:#172b3a}.simple .check summary .pill{margin-left:8px}.simple input[type=file]{display:block;max-width:100%;margin:12px 0}.simple .pill{font-size:14px}.simple .check .pill{font-size:12px}@media(max-width:500px){.simple .fields,.simple .evidence{grid-template-columns:1fr}.simple .result-photo{max-height:360px}}
"""


def escape(value):
    return html.escape(str(value), quote=True)


def badge(status):
    return f'<span class="pill {escape(str(status).lower().replace(" ", "-"))}">{escape(status)}</span>'


def photo_context_options(selected):
    selected = PhotoContext.parse(selected) if selected is not None else None
    labels = {PhotoContext.UNCERTAIN: "I'm not sure", PhotoContext.ISOLATED_PRODUCT: "Only the product",
              PhotoContext.MODEL_WORN: "A person is wearing or holding it"}
    placeholder = '<option value="" selected disabled>Choose an answer</option>' if selected is None else ""
    return placeholder + "".join(f'<option value="{value}" {"selected" if value == selected else ""}>{escape(label)}</option>'
                   for value, label in labels.items())


def page(title, body):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} - Framecheck</title><style>{CSS}</style></head><body><header><a class="brand" href="/">Frame<span>check</span></a><a href="/">Check photo</a><a href="/history">Saved photos</a></header><main>{body}<footer>Photos stay on this computer.<details><summary>Dataset tools</summary><a href="/review">Dataset review</a></details></footer></main></body></html>'''


def available_profiles(*, errors=None):
    profiles = demo_profiles()
    for path in sorted(DATA.glob("*profile.json")):
        try:
            profile, _ = load_profile(path)
            profiles[path.stem] = profile
        except (ValueError, OSError, TypeError, KeyError) as exc:
            if errors is not None:
                errors[path.stem] = f"{path.stem}: unavailable. {exc}"
    return profiles


FORM_FIELDS = {
    "min_short_side": ("Minimum short side (px)", 1, 10000, 1),
    "min_long_side": ("Minimum long side (px)", 1, 10000, 1),
    "min_coverage": ("Minimum frame coverage", 0.01, 1, 0.01),
    "max_coverage": ("Maximum frame coverage", 0.01, 1, 0.01),
    "max_center_offset": ("Maximum center offset", 0, 1, 0.01),
    "min_margin": ("Minimum margin per side", 0, 0.49, 0.005),
    "min_white_fraction": ("Minimum white fraction", 0, 1, 0.01),
    "max_background_distance": ("Maximum background Î”E76", 0, 200, 0.5),
    "white_min_channel": ("Minimum white RGB channel", 0, 255, 1),
    "white_max_spread": ("Maximum white channel spread", 0, 255, 1),
    "min_sharpness": ("Minimum sharpness score (selected profile metric)", 0, 100000, 1),
    "min_contrast": ("Minimum luminance contrast", 0, 255, 1),
    "max_noise": ("Maximum estimated noise sigma", 0, 255, 0.5),
    "max_dark_fraction": ("Maximum dark fraction", 0, 1, 0.01),
    "max_bright_fraction": ("Maximum bright fraction", 0, 1, 0.01),
    "min_exposure_luminance": ("Dark exposure median limit", 0, 255, 1),
    "max_exposure_luminance": ("Bright exposure median limit", 0, 255, 1),
    "review_band": ("Quality threshold review band", 0, 0.5, 0.01),
}


def home(selected=None, error=None, *, photo_context=None, values=None):
    errors = {}
    profiles = available_profiles(errors=errors)
    if selected is not None and selected not in profiles:
        raise ValueError(errors.get(selected, "The selected photo rules are unavailable. Choose an available profile."))
    selected = selected or ("quality-v3-profile" if "quality-v3-profile" in profiles else "Demo White Background")
    if selected not in profiles:
        selected = "quality-v3-profile" if "quality-v3-profile" in profiles else next(iter(profiles))
    values = values or {}
    p = profiles[selected]
    options = "".join(f'<option value="{escape(key)}" {"selected" if key == selected else ""}>{escape(key)}</option>' for key in profiles)
    fields = "".join(f'<div class="field"><label for="{key}">{label}</label><input id="{key}" name="{key}" type="number" min="{low}" max="{high}" step="{step if isinstance(getattr(p, key), int) else "any"}" value="{escape(values.get(key, getattr(p, key)))}" required></div>' for key, (label, low, high, step) in FORM_FIELDS.items())
    notice = f'<div role="alert" class="notice error">{escape(error)}</div>' if error else ""
    warning = ""
    if p.calibrated:
        _, targets_met = load_profile(DATA / (selected + ".json"))
        warning = '<p class="notice">Development-calibrated rules. Review results before use.</p>' if targets_met else '<p class="notice">These experimental rules did not meet validation targets. Review every result.</p>'
    unavailable = "".join(f'<p class="notice">{escape(message)}</p>' for message in errors.values())
    category = values.get("product_category", "clothing" if values.get("shirt_locator") == "segformer" else "general")
    locator = values.get("product_locator", "segformer" if values.get("shirt_locator") == "segformer" else "auto")
    category_options = "".join(f'<option value="{key}" {"selected" if category == key else ""}>{label}</option>' for key, label in [("general", "Other / non-clothing product"), ("footwear", "Footwear"), ("clothing", "Clothing (upper-body garment)")])
    category_field = f'<div class="field"><label for="product_category">2. Product category</label><select name="product_category" id="product_category">{category_options}</select></div>'
    locator_options = "".join(f'<option value="{key}" {"selected" if locator == key else ""} {"disabled hidden" if key == "segformer" and category != "clothing" else ""}>{label}</option>' for key, label in [("auto", "Automatic (default)"), ("foreground", "General foreground (isolated products)"), ("manual", "Mark target product"), ("segformer", "Clothing SegFormer (experimental)")])
    product_controls = f'<div class="field" id="product-locator-field"><label for="product_locator">Product locator</label><select id="product_locator" name="product_locator">{locator_options}</select><p class="caption">Isolated products use general foreground segmentation. SegFormer is only for upper-body clothing. Mark the visible product outline when the target is unclear.</p></div>'
    locator_script = """<script>
const categorySelect=document.getElementById('product_category');
const productLocator=document.getElementById('product_locator');
function updateProductLocator(){
 const clothing=categorySelect.value==='clothing';
 const neural=productLocator.querySelector('option[value=segformer]');
 neural.disabled=!clothing; neural.hidden=!clothing;
 if(!clothing && productLocator.value==='segformer')productLocator.value='auto';
}
categorySelect.addEventListener('change',updateProductLocator);
updateProductLocator();
</script>"""
    return page("Inspect image", f'''<div class="simple"><h1>Check a product photo</h1><p class="muted">Choose a photo. See what needs fixing.</p>{notice}{warning}<form method="post" action="/inspect" enctype="multipart/form-data"><input type="hidden" name="submission_id" value="{uuid4()}"><input type="hidden" name="profile" value="{escape(selected)}"><section class="card"><label for="image">1. Choose a photo</label><input id="image" name="image" type="file" accept="image/jpeg,image/png" required><p class="caption">JPG or PNG. Up to 10 MB and 24 megapixels.</p>{category_field}<div class="field" id="photo-context-field"><label for="photo_context">3. What is in the photo?</label><select id="photo_context" name="photo_context" required>{photo_context_options(photo_context)}</select><p class="caption">Whole-photo checks always run. A specific product region is needed for product-dependent checks.</p></div>{product_controls}<button type="submit">Check photo</button><p class="caption">Your result is saved automatically. Photos stay on this computer.</p></section><details class="card"><summary>Settings (optional)</summary>{unavailable}<div class="field"><label for="preset">Photo rules</label><select id="preset" onchange="location.href='/?profile='+encodeURIComponent(this.value)">{options}</select><p class="caption">Changing photo rules reloads this page. Choose your photo afterward.</p></div><details><summary>Advanced settings</summary><p class="caption">Editing these numbers creates a custom, unevaluated profile. Sharpness metric: {escape(p.sharpness_metric)}. Per-check review bands: {escape(json.dumps(p.quality_review_bands))}. Changing the global review band replaces these per-check bands.</p><div class="fields">{fields}</div></details></details></form>{locator_script}<p class="caption">Demo results can be wrong. Review before making listing decisions.</p></div>''')


def target_chooser(result):
    locator = result["segmentation"].get("product_locator") or result["segmentation"].get("shirt_locator", {})
    targets = locator.get("targets", [])
    if locator.get("status") not in {"ambiguous", "uncertain"} or len(targets) < 2:
        return ""
    identity = escape(result["inspection_id"])
    width, height = result["width"], result["height"]
    markers, options = [], []
    size = max(28, round(width / 30))
    for target in targets:
        number = int(target["id"])
        x, y, w, h = target["bbox"]
        markers.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#ffcc00" stroke-width="{max(3, width // 250)}"/><rect x="{x}" y="{y}" width="{size * 1.4}" height="{size * 1.4}" fill="#ffcc00"/><text x="{x + size / 4}" y="{y + size}" font-size="{size}" fill="#102c3b">{number}</text>')
        options.append(f'<option value="{number}">Product {number}</option>')
    route = "/select-product" if "product_locator" in result["segmentation"] else "/select-shirt"
    field = "product_target" if "product_locator" in result["segmentation"] else "shirt_target"
    return f'''<section class="card" id="shirt-target-picker"><h2>Choose the intended product</h2><p>Several product regions were found. Choose the number on the photo. Selection does not verify the mask or remove DIP review guards.</p><svg viewBox="0 0 {width} {height}" style="display:block;width:100%;max-height:400px" role="img" aria-label="Numbered product candidates"><image href="/evidence/{identity}/overlay.png" width="{width}" height="{height}"/>{''.join(markers)}</svg><form method="post" action="{route}" enctype="multipart/form-data"><input type="hidden" name="source_inspection_id" value="{identity}"><input type="hidden" name="submission_id" value="{uuid4()}"><label for="product_target">Target product</label><select name="{field}" id="product_target" required><option value="" selected disabled>Choose a numbered product</option>{''.join(options)}</select><button type="submit">Inspect this product</button></form><p class="caption">Saves a new result. The original inspection stays unchanged.</p></section>'''


def render_result(record):
    result = json.loads(record["result_json"])
    if result['segmentation'].get('one_click'):
        return modern_ui.render_result(record)
    identity = result["inspection_id"]
    context_label = PHOTO_CONTEXT_LABELS.get(result.get("photo_context"), "Unspecified (legacy inspection)")
    notes = "".join(f'<p>{escape(note)}</p>' for note in result["notes"])
    legacy_warning = ""
    if "photo_context" not in result:
        legacy_warning = '<div class="notice">This saved result predates the product-photo context safeguard. Inspect the image again with confirmed photo context before relying on product-specific decisions.</div>'
    names = {"blur": "Blur", "low_contrast": "Low contrast", "noise": "Grain or noise",
             "underexposure": "Too dark", "overexposure": "Too bright", "low_resolution": "Image size",
             "background_color": "Background color", "background_uniformity": "Uneven background",
             "frame_coverage": "Product size in the photo", "placement": "Product position",
             "margins": "Space around the product", "possible_cropping": "Product may be cut off"}
    attention, passed = [], []
    for check in result["checks"]:
        technical = escape(json.dumps({"measurement": check["measurement"], "threshold": check["threshold"]}, indent=2))
        item = f'<details class="check"><summary>{escape(names.get(check["identifier"], check["identifier"]))} {badge(check["status"])}</summary><p>{escape(check["explanation"])}</p><details><summary>Measurements</summary><pre>{technical}</pre></details></details>'
        (passed if check["status"] in {"Pass", "Disabled"} else attention).append(item)
    message = {"Pass": "No issues found by this check.", "Fail": "Some checks failed. Review the items below.",
               "Manual Review": "We could not confirm everything. Please review this photo."}[result["status"]]
    issues = f'<section class="card"><h2>What needs attention</h2>{"".join(attention)}</section>' if attention else ""
    evidence = "".join(f'<figure><img alt="{label}" src="/evidence/{identity}/{kind}.png"><figcaption>{label}</figcaption></figure>' for kind, label in (("overlay", "Inspection overlay (not the original)"), ("mask", "Estimated foreground mask")))
    passing = f'<details class="card"><summary>Passed checks ({len(passed)})</summary><h2>Technical image quality and rule compliance</h2>{"".join(passed)}</details>' if passed else ""
    warning = '<p class="notice">Development calibration does not guarantee accuracy. Check the reported validation results.</p>' if result["profile"].get("calibrated") else ""
    locator = result["segmentation"].get("product_locator") or result["segmentation"].get("shirt_locator")
    locator_status = '<p id="locator-status" data-mode="foreground">Product locator: General foreground (DIP)</p>'
    if locator:
        state = locator["status"].replace("_", " ").capitalize()
        mode = locator.get("resolved_mode", "segformer")
        label = {"foreground": "General foreground (DIP)", "segformer": "Clothing SegFormer (experimental)", "manual": "User-marked product" if state == "Selected" else "Mark target product"}.get(mode, mode)
        locator_status = f'<p id="locator-status" data-mode="{escape(mode)}">Product locator: {escape(label)}. Status: <strong>{escape(state)}</strong>.</p>'
        if locator.get("source_inspection_id"):
            locator_status += f'<p><a href="/inspection/{escape(locator["source_inspection_id"])}">Choose a different product from the original result</a></p>'
        warning += f'<p class="notice">{escape(locator["reason"])} DIP determines the final verdict.</p>'
    targets = target_chooser(result)
    marking = marking_form(result)
    if locator and locator["status"] in {"candidate", "selected"} and marking:
        marking = '<details><summary>Correct the product outline</summary>' + marking + '</details>'
    targets += marking
    whole_panel = whole_photo_panel(result)
    return page("Inspection result", f'''<div class="simple"><h1>Photo result</h1>{badge(result['status'])}<p>{message}</p><p class="muted">{escape(record['filename'])}</p>{locator_status}{legacy_warning}{warning}{whole_panel}{targets}<div class="actions"><a class="button" href="/">Check another photo</a><a href="/history">Saved photos</a></div>{issues}<section class="card"><h2>Your photo</h2><img class="result-photo" width="{result['width']}" height="{result['height']}" alt="Original uploaded photo" src="/evidence/{identity}/original.png"><p class="caption">Native size: {result['width']} x {result['height']} pixels. Original photo; small images are not enlarged.</p><a href="/evidence/{identity}/original.png" target="_blank" rel="noopener">Open original image</a><details><summary>View overlay and mask</summary><p class="caption">The coloured overlay shows the estimated foreground and box. It does not change the inspected original.</p><div class="evidence">{evidence}</div></details></section>{passing}<details class="card"><summary>Photo details and report</summary><p>Photo context: {escape(context_label)}</p><p>{result['width']} x {result['height']} pixels. Saved {escape(result['created_at'])}.</p>{notes}<a href="/report/{identity}.json">Download JSON report</a><details><summary>Profile and diagnostics</summary><pre>{escape(json.dumps({'profile': result['profile'], 'segmentation': result['segmentation']}, indent=2))}</pre></details></details><p class="caption">Demo results can be wrong. Review before making listing decisions.</p></div>''')


def manifest_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(DATA.resolve()) or path.suffix != ".jsonl":
        raise ValueError("Choose a JSONL manifest inside the project data directory.")
    return path


def review_page(query):
    manifests = sorted(DATA.rglob("*.jsonl")) if DATA.exists() else []
    if not manifests:
        return page("Dataset review", '<div class="eyebrow">Ground truth</div><h1>Review the dataset before measuring performance.</h1><div class="card"><p>No manifest exists yet. Download only Fashion Product Images Small, then run:</p><pre>python -m scripts.dataset audit --dataset data/raw --output data/manifest.jsonl</pre><p>Review originals, confirm duplicate groups, then freeze splits. See README.md for the full workflow.</p></div>')
    path = manifest_path(query.get("manifest", [str(manifests[0].relative_to(ROOT))])[0])
    rows = read_manifest(path)
    if not rows:
        raise ValueError("Manifest contains no images.")
    index = int(query.get("index", [next((i for i, row in enumerate(rows) if not row["reviewed"]), 0)])[0])
    if not 0 <= index < len(rows):
        raise ValueError("Review index is outside the manifest.")
    row = rows[index]
    context_field = f'<div class="field"><label for="photo_context">Photo context</label><select id="photo_context" name="photo_context" required>{photo_context_options(row.get("photo_context", PhotoContext.UNCERTAIN))}</select><p class="caption">Confirm whether the image contains only the product. For model-worn images, do not label a cropped person as a cropped garment.</p></div>'
    rel = str(path.relative_to(ROOT))
    image_url = "/reference?" + urlencode({"manifest": rel, "index": index})
    options = "".join(f'<option value="{escape(str(p.relative_to(ROOT)))}" {"selected" if p == path else ""}>{escape(str(p.relative_to(ROOT)))}</option>' for p in manifests)
    choices = "".join(f'<option value="{i}" {"selected" if i == index else ""}>{i + 1}: {escape(r["id"])} {"(reviewed)" if r["reviewed"] else ""}</option>' for i, r in enumerate(rows))
    labels = ""
    for defect in DEFECTS:
        current = row["labels"][defect]
        values = "".join(f'<option value="{key}" {"selected" if current is value else ""}>{title}</option>' for key, value, title in (("unknown", None, "Uncertain / cannot assess"), ("absent", False, "Absent"), ("present", True, "Present")))
        labels += f'<div class="field"><label for="label-{defect}">{escape(defect.replace("_", " ").capitalize())}</label><select id="label-{defect}" name="label-{defect}">{values}</select></div>'
    reviewer_name = "" if row.get("review_method") == "ai_visual_review" else row.get("reviewer", "")
    progress = sum(r["reviewed"] for r in rows)
    ai_reviews = sum(r["reviewed"] and r.get("review_method") == "ai_visual_review" for r in rows)
    human_reviews = sum(r["reviewed"] and r.get("human_verified") is True for r in rows)
    review_provenance = f'<div class="notice">AI-reviewed: {ai_reviews}. Human-verified: {human_reviews}. Current review method: {escape(row.get("review_method", "unrecorded"))}. AI review is not human-verified ground truth.</div>'
    group = f'<input name="group_id" type="text" value="{escape(row["group_id"])}" required>' if row["split"] is None else f'<p>{escape(row["group_id"])} Â· Split locked: {escape(row["split"])}</p>'
    mask_url = "/reference?" + urlencode({"manifest": rel, "index": index, "mask": "1"})
    mask_title = "AI-reviewed reference mask" if row.get("mask_review_method") == "ai_visual_review" else ("Reviewed reference mask" if row.get("mask_reviewed") else "Unreviewed mask draft")
    mask_preview = f'<details><summary>{mask_title}</summary><img class="reference" alt="{mask_title}" src="{escape(mask_url)}"></details>' if row.get("mask") else ""
    return page("Dataset review", f'''<div class="eyebrow">Ground truth Â· {progress}/{len(rows)} reviewed</div><h1>Reviewed labels. Explicit provenance.</h1>{review_provenance}<form method="get" action="/review" class="card"><div class="fields"><div><label for="manifest">Manifest</label><select id="manifest" name="manifest">{options}</select></div><div><label for="index">Image</label><select id="index" name="index">{choices}</select></div></div><button style="margin-top:15px" type="submit">Open selected image</button></form><div class="notice">Review against Demo White Background rules. Unknown is valid. Transformation names are suggestions, never ground truth. No detector predictions are shown here.</div><form method="post" action="/review" enctype="multipart/form-data"><input type="hidden" name="manifest" value="{escape(rel)}"><input type="hidden" name="index" value="{index}"><div class="grid"><div class="card"><h2>{escape(row['id'])}</h2><img class="reference" alt="Product image for independent review" src="{escape(image_url)}"><p class="caption">{row.get('width','?')} Ã— {row.get('height','?')} px Â· {escape(row.get('kind','original'))} Â· {escape(row.get('category',''))}</p>{mask_preview}{context_field}<div class="field"><label>Product group (confirm duplicate candidates before splitting)</label>{group}</div><details><summary>Add a manually reviewed reference mask</summary><p class="caption">Upload a binary PNG at native dimensions, or enter a polygon as [[x,y], [x,y], ...]. Foreground is white. Polygons support one outline; use uploaded masks for holes or separate objects.</p><div class="field"><label for="mask">Binary reference mask</label><input id="mask" name="mask" type="file" accept="image/png"></div><div class="field"><label for="polygon">Polygon vertices in native pixels (JSON)</label><textarea id="polygon" name="polygon" rows="3" placeholder="[[15,12],[45,12],[45,68],[15,68]]"></textarea></div><label><input type="checkbox" name="mask_confirm" value="yes"> I verified the displayed or new mask against the product outline.</label></details><details><summary>Provenance and transformation</summary><pre>{escape(json.dumps({k:v for k,v in row.items() if k not in {'labels','image'}},indent=2))}</pre></details></div><div class="card"><h2>Visible defects</h2><div class="labels">{labels}</div><div class="field"><label for="reviewer">Reviewer name</label><input id="reviewer" name="reviewer" type="text" value="{escape(reviewer_name)}" required></div><div class="field"><label for="review_notes">Review notes</label><textarea id="review_notes" name="review_notes" rows="3">{escape(row.get('review_notes',''))}</textarea></div><label><input type="checkbox" name="confirmed" value="yes" required> I reviewed every defect label against the image.</label><div class="actions"><button type="submit">Save review &amp; open next</button></div></div></div></form>''')


class Handler(BaseHTTPRequestHandler):
    server_version = f"Framecheck/{PIPELINE_VERSION}"

    def respond(self, body, code=200, content_type="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path
        try:
            if route == "/":
                if query.get('advanced') == ['1'] or 'profile' in query:
                    return self.respond(home(query.get("profile", [None])[0]))
                return self.respond(modern_ui.home())
            if route == "/health":
                return self.respond('{"status":"ok"}', content_type="application/json")
            if route == "/history":
                rows = list_inspections(self.server.database)
                return self.respond(modern_ui.history(rows))
            if route == "/review":
                return self.respond(review_page(query))
            explanation_match = re.fullmatch(r'/explanation/([0-9a-f-]{36})\.json', route)
            if explanation_match:
                identity=str(UUID(explanation_match.group(1)))
                record=load_inspection(identity,self.server.database)
                if not record:
                    return self.respond('{"error":"Inspection not found"}',404,'application/json')
                result=json.loads(record['result_json'])
                if not result['segmentation'].get('one_click'):
                    return self.respond('{"error":"Explanation is available for new one-click reports"}',400,'application/json')
                return self.respond(json.dumps(explain(result)),content_type='application/json')
            if route == "/reference":
                rows = read_manifest(manifest_path(query.get("manifest", [""])[0]))
                index = int(query.get("index", ["-1"])[0])
                if not 0 <= index < len(rows):
                    raise ValueError("Invalid reference index.")
                key = "mask" if "mask" in query else "image"
                if not rows[index].get(key):
                    raise ValueError("Reference mask is not available.")
                rgb, _ = decode_image(image_bytes(rows[index]) if key == "image" else Path(rows[index][key]).read_bytes())
                return self.respond(png_bytes(rgb), content_type="image/png")
            match = re.fullmatch(r"/(inspection|report|evidence)/([0-9a-f-]{36})(?:\.json|/(original|mask|overlay)\.png)?", route)
            if match:
                kind, identity, image = match.groups()
                UUID(identity)
                record = load_inspection(identity, self.server.database)
                if record:
                    if kind == "inspection":
                        return self.respond(render_result(record))
                    if kind == "report":
                        return self.respond(record["result_json"], content_type="application/json")
                    if image:
                        return self.respond(record[f"{image}_png"], content_type="image/png")
            self.respond(page("Not found", '<h1>Page not found.</h1><a href="/">Return to inspection</a>'), 404)
        except (ValueError, OSError, sqlite3.Error, KeyError) as exc:
            self.respond(page("Cannot open page", f'<h1>Cannot open this page.</h1><div class="notice error">{escape(exc)}</div><a href="/">Return to inspection</a>'), 400)

    def parse_form(self):
        if self.headers.get("Origin") and self.headers["Origin"] != f'http://{self.headers.get("Host")}':
            raise ValueError("Submit the form from this local application's own page.")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_REQUEST:
            raise ValueError("Request exceeds the upload limit or is empty.")
        kind = self.headers.get("Content-Type", "")
        if not kind.startswith("multipart/form-data;") or "\r" in kind or "\n" in kind:
            raise ValueError("Expected a multipart image upload form.")
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError("Incomplete upload.")
        message = BytesParser(policy=policy.default).parsebytes(f"Content-Type: {kind}\r\nMIME-Version: 1.0\r\n\r\n".encode() + data)
        fields, files = {}, {}
        if not message.is_multipart():
            raise ValueError("Invalid multipart upload boundary.")
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            body = part.get_payload(decode=True) or b""
            if part.get_filename() is not None:
                files[name] = (part.get_filename(), body)
            else:
                fields[name] = body.decode("utf-8")
        return fields, files

    def do_POST(self):
        fields = {}
        source = None
        try:
            fields, files = self.parse_form()
            if self.path == '/confirm-coverage':
                identity=str(UUID(fields.get('submission_id','')))
                if load_inspection(identity,self.server.database):
                    return self.redirect('/inspection/'+identity)
                source=load_inspection(str(UUID(fields.get('source_inspection_id',''))),self.server.database)
                if source is None or fields.get('coverage_confirm')!='yes':
                    raise ValueError('Explicit coverage confirmation requires a saved inspection.')
                intended=None
                if fields.get('intent_selection')=='yes':
                    intended=[int(name.removeprefix('intended_')) for name,value in fields.items()
                              if name.startswith('intended_') and value=='yes']
                result=amend_inspection(source,confirm_coverage=True,intended_numbers=intended)
                result.inspection_id=identity
                save_inspection(result,source['filename'],self.server.database)
                return self.redirect('/inspection/'+identity)
            if self.path in {"/select-shirt", "/select-product", "/mark-product"}:
                identity = str(UUID(fields.get("submission_id", "")))
                source_id = str(UUID(fields.get("source_inspection_id", "")))
                if load_inspection(identity, self.server.database):
                    return self.redirect("/inspection/" + identity)
                source = load_inspection(source_id, self.server.database)
                if source is None:
                    raise ValueError("The source inspection was not found.")
                if self.path == "/mark-product":
                    if json.loads(source['result_json'])['segmentation'].get('one_click'):
                        points=json.loads(fields.get('polygon','null'))
                        if points is None:raise ValueError('Mark a product outline before submitting.')
                        result=amend_inspection(source,points=points)
                    else:
                        result = select_product(source, polygon=json.loads(fields.get("polygon", "null")))
                elif self.path == "/select-product":
                    result = select_product(source, target_id=int(fields.get("product_target", "")))
                else:
                    result = select_shirt_target(source, int(fields.get("shirt_target", "")))
                result.inspection_id = identity
                save_inspection(result, source["filename"], self.server.database)
                return self.redirect("/inspection/" + identity)
            if self.path == "/inspect":
                identity = str(UUID(fields.get("submission_id", "")))
                if load_inspection(identity, self.server.database):
                    return self.redirect("/inspection/" + identity)
                if fields.get('one_click')=='yes':
                    filename,data=files.get('image',('',b''))
                    result=inspect_auto(data)
                    result.inspection_id=identity
                    save_inspection(result,filename,self.server.database)
                    return self.redirect('/inspection/'+identity)
                profiles = available_profiles()
                if fields.get("profile") not in profiles:
                    raise ValueError("Choose a valid requirement profile.")
                profile = profiles[fields["profile"]]
                changed = False
                for key in FORM_FIELDS:
                    if key in fields:
                        value = int(fields[key]) if type(getattr(profile, key)) is int else float(fields[key])
                        changed |= value != getattr(profile, key)
                        if key == "review_band" and value != profile.review_band:
                            profile.quality_review_bands = {}
                        setattr(profile, key, value)
                if changed:
                    profile.name = "Custom Â· " + profile.name
                    profile.calibrated = False
                    profile.calibration_id = None
                profile.validate()
                filename, data = files.get("image", ("", b""))
                if not fields.get("photo_context"):
                    raise ValueError("Choose what is in the photo, including I am not sure if needed.")
                context = PhotoContext.parse(fields["photo_context"])
                if "product_locator" in fields or "product_category" in fields:
                    result = inspect_product(data, profile, photo_context=context,
                        category=fields.get("product_category", "general"), mode=fields.get("product_locator", "auto"))
                else:
                    # Preserve historical clothing-specific clients; the current form uses category-safe routing.
                    locator = fields.get("shirt_locator", "dip")
                    if locator not in {"dip", "segformer"}:
                        raise ValueError("Choose a supported product locator.")
                    inspect = inspect_with_shirt_locator if locator == "segformer" else inspect_image
                    result = inspect(data, profile, photo_context=context)
                    result.segmentation["requested_locator"] = locator
                if profile.calibrated:
                    _, targets_met = load_profile(DATA / (fields["profile"] + ".json"))
                    result.notes.append("Calibration validation targets met: " + ("yes" if targets_met else "no; experimental rules, review every result"))
                result.inspection_id = identity
                try:
                    save_inspection(result, filename, self.server.database)
                except sqlite3.IntegrityError:
                    if not load_inspection(identity, self.server.database):
                        raise
                except (sqlite3.Error, OSError) as exc:
                    return self.respond(page("Save failed", f'<h1>Inspection ran, but saving failed.</h1><div class="notice error">{escape(exc)}</div><p>No saved result is claimed. Check the database location, then retry.</p><a href="/">Return to inspection</a>'), 500)
                return self.redirect("/inspection/" + identity)
            if self.path == "/review":
                with REVIEW_LOCK:
                    return self.save_review(fields, files)
            self.respond("Unknown action", 404)
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
            if fields.get('one_click')=='yes':
                return self.respond(modern_ui.home(error=str(exc)),400)
            if self.path in {'/confirm-coverage','/mark-product'} and source and json.loads(source['result_json'])['segmentation'].get('one_click'):
                return self.respond(modern_ui.render_result(source,error=str(exc)),400)
            selected = fields.get("profile")
            context = fields.get("photo_context")
            if context not in {value.value for value in PhotoContext}:
                context = None
            if selected not in available_profiles():
                selected = None
            self.respond(home(selected, error=str(exc), photo_context=context, values=fields), 400)

    def save_review(self, fields, files):
        path = manifest_path(fields.get("manifest", ""))
        rows = read_manifest(path)
        index = int(fields.get("index", "-1"))
        if not 0 <= index < len(rows) or fields.get("confirmed") != "yes" or not fields.get("reviewer", "").strip():
            raise ValueError("Choose an image, enter your name, and confirm the review.")
        row = rows[index]
        context = PhotoContext.parse(fields.get("photo_context"))
        if row["split"] is None:
            group = fields.get("group_id", "").strip()
            if not group:
                raise ValueError("Product group cannot be empty.")
        else:
            group = row["group_id"]
        labels = {}
        for defect in DEFECTS:
            value = fields.get("label-" + defect)
            if value not in {"absent", "present", "unknown"}:
                raise ValueError("Every defect needs a review value.")
            labels[defect] = {"absent": False, "present": True, "unknown": None}[value]
        polygon = fields.get("polygon", "").strip()
        mask_data = files.get("mask", ("", b""))[1]
        if polygon and mask_data:
            raise ValueError("Supply either a mask file or polygon, not both.")
        replacement_mask = None
        if polygon or mask_data:
            if fields.get("mask_confirm") != "yes":
                raise ValueError("Confirm that you verified the new reference mask.")
            rgb, _ = decode_image(image_bytes(row))
            h, w = rgb.shape[:2]
            if mask_data:
                decoded, _ = decode_image(mask_data)
                if decoded.shape[:2] != (h, w) or not np.isin(decoded, [0, 255]).all():
                    raise ValueError("Reference mask must be binary (0 or 255) and match native image dimensions.")
                if not np.all(decoded[:, :, 0] == decoded[:, :, 1]) or not np.all(decoded[:, :, 0] == decoded[:, :, 2]):
                    raise ValueError("Reference mask channels must match.")
                mask = decoded[:, :, 0]
            else:
                vertices = json.loads(polygon)
                if not isinstance(vertices, list) or len(vertices) < 3 or any(not isinstance(point, list) or len(point) != 2 or any(type(coordinate) is not int for coordinate in point) or not (0 <= point[0] < w and 0 <= point[1] < h) for point in vertices):
                    raise ValueError("Polygon needs at least three integer [x,y] points inside the image.")
                image = Image.new("L", (w, h))
                ImageDraw.Draw(image).polygon([tuple(point) for point in vertices], fill=255)
                mask = np.array(image)
            if not mask.any() or (mask > 0).all():
                raise ValueError("Reference mask must include both product and background.")
            replacement_mask = png_bytes(mask)
            row["mask_reviewed"] = True
        elif fields.get("mask_confirm") == "yes":
            if not row.get("mask"):
                raise ValueError("No saved mask is available to confirm.")
            rgb, _ = decode_image(image_bytes(row))
            with Image.open(row["mask"]) as image:
                mask = np.array(image.convert("L"))
            if mask.shape != rgb.shape[:2] or not np.isin(mask, [0, 255]).all() or not mask.any() or (mask > 0).all():
                raise ValueError("Saved mask must be native-sized and binary with both product and background pixels.")
            row["mask_reviewed"] = True
        if fields.get("mask_confirm") == "yes":
            row.update(mask_review_method="human_visual_review", mask_human_verified=True, mask_human_confirmed=True)
            row["mask_reviewer"] = fields["reviewer"].strip()
            row["mask_reviewed_at"] = datetime.now(timezone.utc).isoformat()
        row.update(labels=labels, photo_context=context, group_id=group, reviewed=True,
                   review_method="human_visual_review", human_verified=True, human_confirmed=True,
                   photo_context_origin="Human visual review submitted through the review form.", reviewer=fields["reviewer"].strip(),
                   review_notes=fields.get("review_notes", ""), reviewed_at=datetime.now(timezone.utc).isoformat())
        destination = None
        created_mask = False
        try:
            if replacement_mask is not None:
                # Existing versions may still be referenced by frozen manifests or backups.
                destination = DATA / "masks" / (hashlib_id(row["id"]) + "-" + uuid4().hex + ".png")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as handle:
                    created_mask = True
                    handle.write(replacement_mask)
                row["mask"] = str(destination.resolve())
            write_manifest(path, rows)
        except Exception:
            if created_mask:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass  # An orphaned new version must not damage the previous reference mask.
            raise
        following = next((i for i in range(index + 1, len(rows)) if not rows[i]["reviewed"]), next((i for i, r in enumerate(rows) if not r["reviewed"]), index))
        return self.redirect("/review?" + urlencode({"manifest": str(path.relative_to(ROOT)), "index": following}))


def hashlib_id(value):
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def make_server(port=8501, database=DATA / "inspections.sqlite3"):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.database = Path(database)
    return server


def main():
    parser = argparse.ArgumentParser(description="Local product image inspection app")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--database", type=Path, default=DATA / "inspections.sqlite3")
    args = parser.parse_args()
    server = make_server(args.port, args.database)
    print(f"Framecheck is running at http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
