"""Local human mask review only. Does not calculate metrics or change the inspector."""
import argparse
import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit

import numpy as np
from PIL import Image, ImageDraw

from scripts.garment_pilot import digest

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "reports/public-shirts/six-photo-review"


def prepare(root):
    root = Path(root)
    if (root / "review.json").exists():
        raise ValueError("Review already prepared; existing edits must be preserved.")
    results = json.loads((root / "predictions/results.json").read_text(encoding="utf-8"))
    # Points identify the intended item, not its boundary or a reference mask.
    points = {"commons-white-flat": (.5, .55), "commons-blue-shirt": (.5, .6),
              "commons-raglan": (.40, .77), "commons-mundu": (.5, .4),
              "commons-white-worn": (.57, .48), "commons-rack": (.12, .86)}
    cases = []
    for row in results["records"]:
        case = {key: row[key] for key in ("id", "image", "image_sha256", "native_size", "target",
                                          "author", "source_url", "license", "license_url", "context")}
        if digest(row["image"]) != row["image_sha256"]:
            raise ValueError("Original changed: " + row["id"])
        folder = root / "predictions" / row["id"]
        with Image.open(folder / "original.png") as image:
            guide = image.convert("RGB")
        draw = ImageDraw.Draw(guide)
        x, y = points[row["id"]]
        x, y = x * guide.width, y * guide.height
        radius = max(12, min(guide.size) // 35)
        draw.ellipse((x-radius, y-radius, x+radius, y+radius), outline="#ffcf00", width=max(3, radius // 5))
        guide.save(folder / "target-guide.png")
        seed = "baseline" if row["id"] == "commons-white-flat" else "pretrained"
        case["seed"] = "blank" if row["id"] == "commons-rack" else seed
        reference = root / "references" / row["id"]
        reference.mkdir(parents=True, exist_ok=True)
        if case["seed"] == "blank":
            Image.new("L", tuple(row["native_size"]), 0).save(reference / "proposal.png")
        else:
            (reference / "proposal.png").write_bytes((folder / (seed + "-mask.png")).read_bytes())
        case["proposal_sha256"] = digest(reference / "proposal.png")
        case["prediction_sha256"] = {kind: digest(folder / (kind + "-mask.png"))
                                      for kind in ("baseline", "pretrained")}
        cases.append(case)
    (root / "review.json").write_text(json.dumps({"cases": cases,
        "reference_policy": "Unverified model-assisted proposals; rack starts blank. Human approval is required. "
                            "Assisted references are not a blind independent benchmark. No IoU before approval."}, indent=2), encoding="utf-8")


def current(root, case):
    folder = Path(root) / "references" / case["id"]
    versions = sorted(p for p in folder.glob("[0-9]*") if p.is_dir() and (p / "review.json").is_file())
    if versions:
        metadata = json.loads((versions[-1] / "review.json").read_text(encoding="utf-8"))
        if digest(versions[-1] / "mask.png") != metadata["mask_sha256"]:
            raise ValueError("Saved mask changed outside the editor; review it again.")
        return metadata, versions[-1] / "mask.png"
    proposal = folder / "proposal.png"
    if digest(proposal) != case["proposal_sha256"]:
        raise ValueError("Initial proposal changed outside the editor.")
    return {"revision": 0, "approved": False, "reviewer": "", "notes": "", "seed": case["seed"]}, proposal


def save_review(root, case, payload):
    """Save immutable revisions; only an explicit human action can mark a mask approved."""
    previous, _ = current(root, case)
    if type(payload.get("revision")) is not int or payload["revision"] != previous["revision"]:
        raise ValueError("Stale revision. Reload the photo before saving.")
    if digest(case["image"]) != case["image_sha256"]:
        raise ValueError("Original image changed; cannot approve this mask.")
    approved = payload.get("approved")
    if type(approved) is not bool:
        raise ValueError("Approval must be an explicit boolean.")
    reviewer = payload.get("reviewer", "").strip()
    notes = payload.get("notes", "").strip()
    if len(reviewer) > 120 or len(notes) > 4000:
        raise ValueError("Reviewer or notes are too long.")
    if approved and (not reviewer or payload.get("confirmed") is not True):
        raise ValueError("Approval requires your name and the visible-fabric confirmation.")
    if payload.get("seed") not in {"baseline", "pretrained", "blank"}:
        raise ValueError("Unknown proposal source.")
    encoded = payload.get("mask", "")
    prefix = "data:image/png;base64,"
    if not isinstance(encoded, str) or not encoded.startswith(prefix):
        raise ValueError("Expected a PNG mask.")
    raw = base64.b64decode(encoded[len(prefix):], validate=True)
    with Image.open(BytesIO(raw)) as image:
        if image.format != "PNG" or list(image.size) != case["native_size"]:
            raise ValueError("Mask must be a native-size PNG.")
        rgba = np.asarray(image.convert("RGBA"))
    gray = rgba[:, :, 0]
    if not (np.isin(gray, [0, 255]).all() and (rgba[:, :, 1] == gray).all()
            and (rgba[:, :, 2] == gray).all() and (rgba[:, :, 3] == 255).all()):
        raise ValueError("Mask must contain opaque black and white pixels only.")
    if approved and (not gray.any() or (gray == 255).all()):
        raise ValueError("An empty or full-frame mask cannot be approved as a shirt.")
    parent = Path(root) / "references" / case["id"]
    # Skip incomplete revisions left by an interrupted write; never overwrite evidence.
    revision = 1 + max([previous["revision"]] + [int(p.name) for p in parent.iterdir() if p.is_dir() and p.name.isdigit()])
    folder = parent / f"{revision:06d}"
    folder.mkdir()
    Image.fromarray(gray).save(folder / "mask.png")
    metadata = {"revision": revision, "approved": approved, "reviewer": reviewer, "notes": notes,
                "confirmed_visible_fabric": payload.get("confirmed") is True,
                "seed": payload["seed"], "initial_seed": case["seed"],
                "provenance": "Human-reviewed model-assisted reference" if approved else "Unverified draft",
                "image_sha256": case["image_sha256"], "native_size": case["native_size"],
                "mask_sha256": digest(folder / "mask.png"), "target": case["target"],
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "independent_blind_reference": False}
    # Write metadata last: an interrupted save never becomes the current revision.
    temporary = folder / "review.tmp"
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary.replace(folder / "review.json")
    return metadata


def make_server(root, port=8511):
    root = Path(root).resolve()
    catalog = json.loads((root / "review.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in catalog["cases"]}
    writes = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, data, content_type="application/json"):
            if isinstance(data, dict):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def allowed_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.allowed_host():
                return self.reply(403, {"error": "Use the 127.0.0.1 review URL."})
            path = urlsplit(self.path).path
            try:
                if path == "/":
                    return self.reply(200, Path(__file__).with_suffix(".html").read_bytes(), "text/html; charset=utf-8")
                if path == "/api":
                    return self.reply(200, {**catalog, "cases": [{**case, "review": current(root, case)[0]} for case in cases.values()]})
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[0] == "assets" and parts[1] in cases:
                    case = cases[parts[1]]
                    kind = parts[2]
                    if kind == "reference":
                        _, asset = current(root, case)
                    elif kind in {"original", "target-guide", "baseline-mask", "pretrained-mask", "baseline-overlay", "pretrained-overlay"}:
                        asset = root / "predictions" / case["id"] / (kind + ".png")
                    else:
                        return self.reply(404, {"error": "Unknown asset."})
                    return self.reply(200, asset.read_bytes(), "image/png")
                return self.reply(404, {"error": "Unknown review page."})
            except (OSError, ValueError) as error:
                return self.reply(400, {"error": str(error)})

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.allowed_host() or self.headers.get("Origin") != origin:
                return self.reply(403, {"error": "Only this local editor can save references."})
            parts = urlsplit(self.path).path.strip("/").split("/")
            if len(parts) != 2 or parts[0] != "save" or parts[1] not in cases:
                return self.reply(404, {"error": "Unknown case."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 15_000_000 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected bounded JSON request.")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Expected a JSON object.")
                with writes:
                    saved = save_review(root, cases[parts[1]], payload)
                self.reply(200, saved)
            except (OSError, ValueError, TypeError, AttributeError) as error:
                self.reply(400, {"error": str(error)})

    # Browsers preconnect idle sockets; concurrent readers must not block image requests.
    # The write lock still prevents two tabs from saving the same revision.
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--port", type=int, default=8511)
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        prepare(args.root)
        return
    with make_server(args.root, args.port) as server:
        print(f"Human review: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
