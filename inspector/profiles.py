"""Validate profile provenance separately from achieved validation targets."""
import hashlib
import json
from pathlib import Path, PureWindowsPath

from inspector import PIPELINE_VERSION
from inspector.models import InspectionProfile


def load_profile(path: Path) -> tuple[InspectionProfile, bool | None]:
    profile = InspectionProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if not profile.calibrated:
        return profile, None
    report = json.loads(path.with_suffix(".calibration.json").read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Calibration record must be an object.")
    if report.get("profile_sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError("Profile changed after calibration.")
    if report.get("pipeline_version") != PIPELINE_VERSION:
        raise ValueError("Profile belongs to an older inspection version. Recalibration is required.")
    dimensions = {key: getattr(profile, key) for key in (
        "calibration_short_min", "calibration_short_max", "calibration_long_min", "calibration_long_max")}
    if report.get("calibration_dimensions") != dimensions:
        raise ValueError("Profile size support does not match its calibration record.")
    if type(report.get("validation_constraints_met")) is not bool:
        raise ValueError("Calibration record does not state whether validation targets were met.")
    development = report.get("development_sha256", "")
    if not isinstance(development, str) or len(development) != 64 or any(c not in "0123456789abcdef" for c in development) or profile.calibration_id != development[:16]:
        raise ValueError("Profile calibration ID does not match its development record.")
    hashes = report.get("pipeline_files_sha256")
    core = {"pipeline.py", "segmentation.py", "images.py", "models.py", "__init__.py"}
    if not isinstance(hashes, dict) or not core <= {PureWindowsPath(name).name for name in hashes}:
        raise ValueError("Calibration record is missing inspection-code hashes.")
    for name, expected in hashes.items():
        # UI/storage edits do not change the measured inspection method.
        if PureWindowsPath(name).name in core:
            current = Path(__file__).parent / PureWindowsPath(name).name
            if hashlib.sha256(current.read_bytes()).hexdigest() != expected:
                raise ValueError("Inspection code changed after calibration.")
    return profile, report["validation_constraints_met"]
