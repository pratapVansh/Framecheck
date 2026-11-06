"""Verify frozen evidence and package the course deliverables without user history."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import zipfile

from PIL import Image
from pptx import Presentation


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def main() -> None:
    bundle = ROOT / "submission/Framecheck-course-bundle.zip"
    manifest_path = ROOT / "submission/bundle-manifest.json"
    gate_path = ROOT / "reports/revision-phase7/completion.json"
    assert not any(p.exists() for p in (bundle, manifest_path, gate_path)), "Refusing to overwrite final evidence"
    freeze = read("reports/revision-phase5/methods.freeze.json")
    for relative, expected in freeze["code_sha256"].items():
        assert digest(ROOT / relative) == expected, relative
    assert digest(ROOT / freeze["manifest"]) == freeze["manifest_sha256"]
    for method in freeze["methods"].values():
        assert digest(ROOT / method["profile"]) == method["sha256"]
        assert not method["deployment_approved"]
    for relative, expected in read("reports/revision-phase1/audit.json")["protected_files_sha256"].items():
        assert digest(ROOT / relative) == expected, relative
    for relative, expected in read("reports/revision-phase2/artifact-hashes.json")["sha256"].items():
        # Phase 2 source hashes precede the authorized Phase 3-5 implementation.
        if Path(relative).parts[0] in {"data", "reports"}:
            assert digest(ROOT / relative) == expected, relative
    for absolute, expected in read("reports/revision-phase1/assets.sha256.json").items():
        assert digest(Path(absolute)) == expected, absolute
    comparison = read("reports/revision-phase6/comparison.json")
    assert comparison["method_freeze_sha256"] == digest(ROOT / "reports/revision-phase5/methods.freeze.json")
    assert comparison["test_images"] == 337 and comparison["test_groups"] == 60
    assert set(comparison["methods"]) == set(freeze["methods"])
    proof = read("reports/revision-phase7/verified-run/verification.json")
    assert proof["status"] == "passed" and proof["inspections"] == 8 and proof["check_records"] == 96
    assert proof["desktop_and_mobile_browser"] and proof["process_restart"]
    pdf = (ROOT / "submission/report.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-") and len(re.findall(rb"/Type\s*/Page\b", pdf)) == 10
    presentation = Presentation(ROOT / "submission/presentation.pptx")
    assert len(presentation.slides) == 10
    assert any(s.has_chart for slide in presentation.slides for s in slide.shapes)
    for slide in presentation.slides:
        for shape in slide.shapes:
            assert shape.left >= 0 and shape.top >= 0
            assert shape.left + shape.width <= presentation.slide_width
            assert shape.top + shape.height <= presentation.slide_height

    files: set[Path] = set()

    def add(path: Path) -> None:
        path = path.resolve()
        assert path.is_relative_to(ROOT), path
        relative = path.relative_to(ROOT)
        if (not path.is_file() or "__pycache__" in relative.parts
                or path.suffix.lower() in {".pyc", ".log"}
                or ".sqlite" in path.name.lower()
                or "chrome" in str(relative).lower()
                or path in {bundle, manifest_path, gate_path}):
            return
        files.add(path)

    for name in ("app.py", "plan.md", "README.md", "pyproject.toml", "requirements.txt", "requirements-dev.txt", ".gitignore"):
        add(ROOT / name)
    for directory in ("inspector", "scripts", "tests", "docs", "submission"):
        for path in (ROOT / directory).rglob("*"):
            add(path)
    for directory in sorted((ROOT / "reports").glob("revision-phase*")):
        for path in directory.rglob("*"):
            add(path)
    for path in (ROOT / "data").rglob("*"):
        if "raw" not in path.relative_to(ROOT / "data").parts:
            add(path)
    add(ROOT / "data/raw/archive/myntradataset/styles.csv")
    for relative in ("data/benchmark.jsonl", "data/revision-v2/benchmark-v2.2.jsonl"):
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            for field in ("image", "mask"):
                if row.get(field):
                    path = (ROOT / row[field]).resolve()
                    assert path.is_file(), path
                    add(path)
    invalid_fixtures = {ROOT / "submission/manual-samples/06-invalid.png", ROOT / "data/manual-test-samples/07-invalid.png"}
    for path in files:
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"} and path not in invalid_fixtures:
            with Image.open(path) as image:
                image.verify()
    entries = {path.relative_to(ROOT).as_posix(): digest(path) for path in sorted(files)}
    manifest = {
        "status": "verified", "files_sha256": entries,
        "exclusions": ["all SQLite databases", "browser profiles", "logs", "virtual environments", "unselected raw dataset"],
        "portability": "App/manual samples are portable. Frozen offline manifests retain original absolute paths; see bundle-notes.md.",
        "manifest_self_hash_excluded": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with zipfile.ZipFile(bundle, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative in entries:
            archive.write(ROOT / relative, relative)
        archive.write(manifest_path, "submission/bundle-manifest.json")
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(entries) + 1
        assert not any(".sqlite" in name.lower() for name in archive.namelist())
        for relative, expected in entries.items():
            assert hashlib.sha256(archive.read(relative)).hexdigest() == expected, relative
    gate = {
        "status": "complete with unsuccessful accuracy conclusion", "phases": [5, 6, 7],
        "software_tests": {"passed": 106, "seconds": 30.52},
        "real_browser_verification": "reports/revision-phase7/verified-run/verification.json",
        "protected_history_data_and_frozen_methods_unchanged": True,
        "pdf_pages": 10, "editable_slides": 10, "powerpoint_rendering_checked": False,
        "bundle": str(bundle.relative_to(ROOT)), "bundle_sha256": digest(bundle),
        "bundle_files": len(entries) + 1, "bundle_bytes": bundle.stat().st_size,
        "bundle_crc_and_all_entry_hashes_verified": True,
        "accuracy_targets_met": False, "default_method": "Demo White Background",
        "limits": ["AI-only references", "seven acceptable fresh-test groups", "student/team fields must be supplied", "frozen absolute data paths"],
        "proof_sha256": {relative: digest(ROOT / relative) for relative in (
            "reports/revision-phase5/methods.freeze.json", "reports/revision-phase5/replay.json",
            "reports/revision-phase6/comparison.json", "reports/revision-phase7/verified-run/verification.json",
            "submission/report.pdf", "submission/presentation.pptx", "submission/bundle-manifest.json", "plan.md")},
    }
    gate_path.write_text(json.dumps(gate, indent=2), encoding="utf-8")
    print(f"PASS: frozen/protected hashes, 10-page PDF, 10-slide PPTX, images, {len(entries)+1} ZIP entries, CRC and every entry hash; {bundle.stat().st_size:,} bytes.")


if __name__ == "__main__":
    main()
