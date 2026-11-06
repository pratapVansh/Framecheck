"""Version AI-reviewed fresh-source annotations before any final test inference."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from inspector.benchmark import rules_hash, write_manifest
from inspector.images import decode_image, png_bytes
from inspector.models import DEFECTS, InspectionProfile
from scripts.prepare_quality_revision import DATA, REPORT, padded


def main():
    assert not (DATA / "test.jsonl").exists(), "Refusing to replace test references"
    sources = json.loads((DATA / "test-sources.draft.json").read_text())
    worn = {"13878", "16416", "14625", "22045", "37798", "15834", "40092"}
    destination = DATA / "fresh-test"
    destination.mkdir(exist_ok=False)
    rows = []
    rng = np.random.default_rng(20261105)
    for item in sources:
        rgb, _ = decode_image(Path(item["image"]).read_bytes())
        context = "model_worn" if item["id"] in worn else "isolated_product"
        baseline = dict.fromkeys(DEFECTS)
        baseline["low_resolution"] = False
        if context == "isolated_product":
            baseline.update(blur=False, underexposure=False)
            if item["id"] not in {"27668", "53586", "51538"}:
                baseline.update(low_contrast=False, overexposure=False)
        variants = {
            "original": (rgb, None),
            "gaussian": (cv2.GaussianBlur(rgb, (5,5), 1.0), "blur"),
            "motion": (cv2.filter2D(rgb, -1, np.ones((1,7), np.float32)/7), "blur"),
            "noise": (np.clip(rgb.astype(float)+rng.normal(0,20,rgb.shape),0,255).astype(np.uint8), "noise"),
            "contrast": (np.clip((rgb.astype(float)-128)*.15+128,0,255).astype(np.uint8), "low_contrast"),
            "dark": (np.clip(rgb.astype(float)*.08,0,255).astype(np.uint8), "underexposure"),
            "bright": (np.clip(rgb.astype(float)*2.8+130,0,255).astype(np.uint8), "overexposure"),
        }
        sheet = Image.new("RGB", (840, 220), "#edf1f4")
        draw = ImageDraw.Draw(sheet)
        for index,(name,(pixels,defect)) in enumerate(variants.items()):
            sheet.paste(Image.fromarray(pixels).resize((120,160),Image.Resampling.NEAREST),(index*120,35))
            draw.text((index*120+3,5),item["id"]+" "+name,fill="black")
            for canvas in (False, True):
                result = padded(pixels) if canvas else pixels
                identity = item["id"]+"__"+name+("__canvas96" if canvas else "")
                path = destination / (identity+".png");path.write_bytes(png_bytes(result))
                labels = deepcopy(baseline) if defect is None else dict.fromkeys(DEFECTS)
                labels["low_resolution"] = False
                if defect is not None and context == "isolated_product":
                    labels[defect] = True
                rows.append({"id":identity,"source_id":item["id"],"group_id":item["id"],"source_hash":item["image_sha256"],
                             "image":str(path),"image_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"width":result.shape[1],"height":result.shape[0],
                             "category":item["category"],"kind":"original" if name=="original" and not canvas else "control" if name=="original" else "synthetic",
                             "split":"test","photo_context":context,"labels":labels,"reviewed":True,"mask":None,"mask_reviewed":False,
                             "rules_hash":rules_hash(InspectionProfile()),"review_method":"AI_visual_source_and_controlled_transform_review",
                             "reviewer":"Codex AI; not human-verified","human_verified":False,"human_confirmed":False,
                             "review_notes":"Source/transform contact sheets reviewed without detector scores. Unknown truth remains unknown. Noise-negative and compliance truth unavailable. All derivatives share the source split.",
                             "transform":{"name":name,"exact_pixel_96_padding":canvas,"seed":20261105}})
        sheet.save(REPORT / ("test-transform-review-"+item["id"]+".png"))
    write_manifest(DATA / "test.jsonl",rows)
    freeze={"source_groups":len(sources),"records":len(rows),"test_sha256":hashlib.sha256((DATA/"test.jsonl").read_bytes()).hexdigest(),
            "development_sha256":hashlib.sha256((DATA/"development.jsonl").read_bytes()).hexdigest(),
            "test_predictions_run":False,"annotation_method":"AI review; no human verification",
            "limitations":["Only small native photos and non-resampled padding controls","Transforms are synthetic whole-image stresses","No negative noise or compliance references","Model-worn quality truth unknown","Normal-sized calibration unavailable"]}
    (DATA/"split.freeze.json").write_text(json.dumps(freeze,indent=2))
    print(freeze)


if __name__ == "__main__":
    main()
