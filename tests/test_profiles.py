import hashlib
import json
from pathlib import Path

import pytest

from inspector import PIPELINE_VERSION, web
from inspector.models import InspectionProfile
from inspector.profiles import load_profile
from test_web import server, request, multipart


def calibrated_file(directory, feasible=False):
    path = directory / "checked-profile.json"
    profile = InspectionProfile(calibrated=True, calibration_id="a" * 16)
    path.write_text(json.dumps(profile.to_dict()))
    report = {"profile_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "pipeline_version": PIPELINE_VERSION, "development_sha256": "a" * 64,
              "calibration_dimensions": {k:v for k,v in profile.to_dict().items() if k.startswith("calibration_") and k != "calibration_id"},
              "validation_constraints_met": feasible,
              "pipeline_files_sha256": {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(web.__file__).parent.glob("*.py")}}
    path.with_suffix(".calibration.json").write_text(json.dumps(report))
    return path


@pytest.mark.parametrize('style',['windows','posix'])
def test_historical_calibration_paths_are_portable(tmp_path,style):
    path=calibrated_file(tmp_path)
    sidecar=path.with_suffix('.calibration.json')
    report=json.loads(sidecar.read_text())
    prefix='C:\\original-workspace\\inspector\\' if style=='windows' else '/original-workspace/inspector/'
    report['pipeline_files_sha256']={prefix+name:digest for name,digest in report['pipeline_files_sha256'].items()}
    sidecar.write_text(json.dumps(report))
    profile,targets=load_profile(path)
    assert profile.calibrated and targets is False


@pytest.mark.parametrize("damage", ["missing", "hash", "version", "dimensions", "code", "identifier", "shape"])
def test_untrusted_calibration_is_unavailable(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(web, "DATA", tmp_path)
    path = calibrated_file(tmp_path)
    sidecar = path.with_suffix(".calibration.json")
    report = json.loads(sidecar.read_text())
    if damage == "missing":
        sidecar.unlink()
    else:
        if damage == "hash": report["profile_sha256"] = "incorrect"
        if damage == "version": report["pipeline_version"] = "old"
        if damage == "dimensions": report["calibration_dimensions"]["calibration_short_max"] += 1
        if damage == "code": report["pipeline_files_sha256"]["pipeline.py"] = "incorrect"
        if damage == "identifier": report["development_sha256"] = "b" * 64
        if damage == "shape": report = []
        sidecar.write_text(json.dumps(report))
    errors = {}
    assert path.stem not in web.available_profiles(errors=errors)
    assert path.stem in errors
    with pytest.raises(ValueError, match="unavailable"):
        web.home(path.stem)


def test_valid_experimental_profile_and_error_state(server, tmp_path, monkeypatch, product_bytes):
    monkeypatch.setattr(web, "DATA", tmp_path)
    path = calibrated_file(tmp_path)
    assert load_profile(path)[1] is False
    code, _, body = request(server, "GET", "/?profile=" + path.stem)
    assert code == 200 and b"did not meet validation targets" in body
    fields = {"profile":path.stem, "submission_id":"e0e4bff0-1101-4ab2-9d6f-c8e4d9e07655", "photo_context":"isolated_product",
              "min_coverage":"0.95", "max_coverage":"0.4"}
    form, kind = multipart(fields, product_bytes)
    code, _, body = request(server, "POST", "/inspect", form, {"Content-Type":kind})
    assert code == 400
    assert b'name="profile" value="checked-profile"' in body
    assert b'<option value="isolated_product" selected>' in body
    assert b'value="0.95"' in body
    assert request(server,"GET","/?profile=missing")[0] == 400


def test_missing_browser_context_is_rejected(server, product_bytes):
    form, kind = multipart({"profile":"Demo White Background", "submission_id":"e0e4bff0-1101-4ab2-9d6f-c8e4d9e07656"}, product_bytes)
    code, _, body = request(server,"POST","/inspect",form,{"Content-Type":kind})
    assert code == 400 and b"Choose what is in the photo" in body


def test_review_reason_codes_preserve_independent_failures(product_bytes):
    from inspector.pipeline import inspect_image
    result = inspect_image(product_bytes)
    assert all(c.reason_code == "photo_context" for c in result.checks if c.identifier != "low_resolution")
    assert result.checks[0].reason_code is None
