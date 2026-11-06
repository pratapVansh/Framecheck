"""Prepare licensed original shirt sources and an explicit human verification queue."""
import argparse
import csv
import json
from io import BytesIO
from pathlib import Path
import urllib.error
import urllib.request

from PIL import Image

from inspector.images import MAX_BYTES, decode_image
from scripts.garment_pilot import ROOT, digest


def validate_original(data, expected_size):
    with Image.open(BytesIO(data)) as image:
        if list(image.size) != expected_size:
            raise ValueError(f"Expected original {expected_size}, received {list(image.size)}; reject preview/resized substitute.")
    return decode_image(data)[0]


def prepare(catalog, output, *, download=False):
    sources = json.loads(Path(catalog).read_text(encoding="utf-8"))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest, queue, inventory = [], [], []
    for source in sources:
        if not all(source.get(k) for k in ("id", "source_url", "original_url", "author", "license", "license_url", "reuse_conditions")):
            raise ValueError("Every source requires its own attribution and license record.")
        path = ROOT / source["local_file"]
        error = None
        if not path.exists() and download:
            try:
                request = urllib.request.Request(source["original_url"], headers={"User-Agent": "Framecheck-local-research-pilot/1.0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    data = response.read(MAX_BYTES + 1)
                validate_original(data, source["expected_size"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                error = str(exc)
        available = path.is_file()
        row = {**source, "available": available, "download_error": error}
        if available:
            rgb = validate_original(path.read_bytes(), source["expected_size"])
            row.update(image_sha256=digest(path), native_size=[rgb.shape[1], rgb.shape[0]])
            manifest.append({**row, "image": str(path.resolve()), "source_split": "public_diagnostic_pilot",
                             "context": source["planned_context"], "metadata": {"source": source["source_url"]},
                             "reference_mask": None, "reference_provenance": None, "reference_human_verified": False,
                             "human_verified": False, "selection_method": "Publisher source/license audit; available original AI-reviewed only",
                             "occlusion": "hanger_occlusion" if source["id"] == "commons-rack" else "unverified",
                             "multiple_garments": source["id"] == "commons-rack", "multiple_target_shirts": source["id"] == "commons-rack",
                             "notes": [source["case"], "No reference mask: human annotation and independent verification required."]})
        inventory.append(row)
        queue.append({"id": source["id"], "image": str(path), "source_available": available,
                      "target": source["target"], "source_url": source["source_url"], "license": source["license"],
                      "status": "needs_human_annotation" if available else "needs_original_download_then_visual_screening",
                      "reference_mask": None, "human_verified": False, "reviewer": None, "reviewed_at": None,
                      "instructions": "Confirm real photograph, intended shirt and context; trace visible shirt only, preserving holes and occlusion. Exclude skin, hangers and other garments. Review boundaries independently before accepting a mask. Do not promote a model proposal or a missing/empty mask to reference truth."})
    (output / "manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in manifest), encoding="utf-8")
    (output / "human-review-queue.jsonl").write_text("".join(json.dumps(r) + "\n" for r in queue), encoding="utf-8")
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    fields = ["id", "title", "author", "source_url", "original_url", "license", "license_url", "reuse_conditions", "available", "local_file"]
    with (output / "sources.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(inventory)
    print(json.dumps({"sources": len(sources), "available_originals": len(manifest), "missing_originals": len(sources) - len(manifest),
                      "human_verified_masks": 0, "manifest": str(output / "manifest.jsonl")}))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/public-shirts/sources.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    prepare(args.catalog, args.output, download=args.download)


if __name__ == "__main__":
    main()
