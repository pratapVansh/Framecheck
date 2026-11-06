import http.client
from html.parser import HTMLParser
import json
import threading
from uuid import uuid4

import pytest

from inspector.storage import list_inspections
from inspector.web import home, make_server


class VisibleControls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details_depth = 0
        self.controls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details":
            self.details_depth += 1
        if tag in {"input", "select", "button"} and not self.details_depth and attrs.get("type") != "hidden":
            self.controls.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == "details":
            self.details_depth -= 1


def test_simple_home_has_category_context_locator_and_submit_visible():
    parser = VisibleControls()
    parser.feed(home())
    assert [(tag, attrs.get("name")) for tag, attrs in parser.controls] == [
        ("input", "image"), ("select", "product_category"), ("select", "photo_context"),
        ("select", "product_locator"), ("button", None)]
    assert parser.details_depth == 0
    assert '<option value="" selected disabled>' in home()


def multipart(fields, data=None, filename="product.png"):
    boundary = "framecheck-test-boundary"
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    if data is not None:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def server(tmp_path):
    server = make_server(0, tmp_path / "web.sqlite3")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request(method, path, body, headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_upload_save_history_evidence_and_idempotency(server, product_bytes):
    code, _, body = request(server, "GET", "/")
    assert code == 200 and b"one-click-upload" in body
    identity = str(uuid4())
    form, content_type = multipart({"submission_id": identity, "profile": "Demo White Background", "photo_context": "isolated_product"}, product_bytes, "<script>alert(1)</script>.png")
    for _ in range(2):
        code, headers, _ = request(server, "POST", "/inspect", form, {"Content-Type": content_type})
        assert code == 303 and headers["Location"] == "/inspection/" + identity
    assert len(list_inspections(server.database)) == 1
    code, _, body = request(server, "GET", "/inspection/" + identity)
    assert code == 200 and b"Technical image quality" in body
    assert b"Passed checks (12)" in body and b"What needs attention" not in body
    assert b"<script>alert(1)</script>" not in body and b"&lt;script&gt;" in body
    code, _, body = request(server, "GET", "/history")
    assert code == 200 and identity.encode() in body
    code, headers, body = request(server, "GET", "/evidence/" + identity + "/mask.png")
    assert code == 200 and headers["Content-Type"] == "image/png" and body.startswith(b"\x89PNG")
    code, _, body = request(server, "GET", "/report/" + identity + ".json")
    assert code == 200 and json.loads(body)["status"] == "Pass"
    assert json.loads(body)["photo_context"] == "isolated_product"


def test_validation_and_local_origin(server, product_bytes):
    fields = {"submission_id": str(uuid4()), "profile": "Demo White Background", "min_coverage": "0.95", "max_coverage": "0.4"}
    form, content_type = multipart(fields, product_bytes)
    code, _, body = request(server, "POST", "/inspect", form, {"Content-Type": content_type})
    assert code == 400 and b"Coverage" in body
    code, _, _ = request(server, "POST", "/inspect", form, {"Content-Type": content_type, "Origin": "https://another-site.example"})
    assert code == 400 and list_inspections(server.database) == []
    code, _, _ = request(server, "GET", "/reference?manifest=../private.jsonl&index=0")
    assert code == 400


def test_missing_history_record_and_health(server):
    assert request(server, "GET", "/health")[0] == 200
    assert request(server, "GET", "/inspection/" + str(uuid4()))[0] == 404


def test_fractional_calibrated_thresholds_allow_browser_submission(monkeypatch):
    # Calibrated percentiles need not align with the demonstration spinner step.
    from inspector.models import InspectionProfile
    monkeypatch.setattr("inspector.web.available_profiles", lambda **kwargs: {
        "fractional": InspectionProfile(min_sharpness=3135.408935546875, max_noise=3.2246600627899165)})
    document = home("fractional")
    import re
    for key in ("min_sharpness", "max_noise", "review_band"):
        field = re.search(r'<input id="' + key + r'"[^>]+>', document).group()
        assert 'step="any"' in field


@pytest.mark.parametrize("context", ["uncertain", "model_worn"])
def test_unconfirmed_product_scope_is_saved_as_review(server, product_bytes, context):
    identity = str(uuid4())
    fields = {"submission_id": identity, "profile": "Demo White Background"}
    if context is not None:
        fields["photo_context"] = context
    form, kind = multipart(fields, product_bytes)
    assert request(server, "POST", "/inspect", form, {"Content-Type": kind})[0] == 303
    code, _, payload = request(server, "GET", "/report/" + identity + ".json")
    report = json.loads(payload)
    assert code == 200 and report["status"] == "Manual Review"
    assert report["photo_context"] == (context or "uncertain")
    assert not report["segmentation"]["product_scope_confirmed"]
    assert all(check["status"] == "Manual Review" for check in report["checks"] if check["identifier"] != "low_resolution")
    code, _, body = request(server, "GET", "/inspection/" + identity)
    assert code == 200 and b"Photo context:" in body and b"Estimated foreground mask" in body
    assert b"What needs attention" in body and b"View overlay and mask" in body


def test_invalid_scope_is_rejected_without_saving(server, product_bytes):
    form, kind = multipart({"submission_id": str(uuid4()), "profile": "Demo White Background", "photo_context": "guess"}, product_bytes)
    code, _, body = request(server, "POST", "/inspect", form, {"Content-Type": kind})
    assert code == 400 and b"Photo context" in body
    assert list_inspections(server.database) == []


def test_review_annotations_and_reference_masks(server, tmp_path, monkeypatch, product_bytes):
    from inspector import web
    from inspector.benchmark import read_manifest, rules_hash, write_manifest
    from inspector.models import DEFECTS, InspectionProfile
    directory = tmp_path / "review-data"
    directory.mkdir()
    monkeypatch.setattr(web, "DATA", directory)
    image = directory / "product.png"
    image.write_bytes(product_bytes)
    manifest = directory / "manifest.jsonl"
    item = {"id": "1", "source_id": "1", "group_id": "1", "image": str(image), "split": None,
            "labels": dict.fromkeys(DEFECTS), "reviewed": False, "mask": None,
            "rules_hash": rules_hash(InspectionProfile()), "width": 60, "height": 80}
    write_manifest(manifest, [item])
    query = "/review?manifest=" + str(manifest.relative_to(web.ROOT)).replace("\\", "/")
    code, _, body = request(server, "GET", query)
    assert code == 200 and b"Visible defects" in body
    fields = {"manifest": str(manifest), "index": "0", "reviewer": "test reviewer", "confirmed": "yes",
              "photo_context": "isolated_product",
              "group_id": "confirmed-product", "mask_confirm": "yes", "polygon": "[[15,12],[44,12],[44,67],[15,67]]"}
    fields.update({"label-" + defect: "absent" for defect in DEFECTS})
    form, kind = multipart(fields)
    code, _, body = request(server, "POST", "/review", form, {"Content-Type": kind})
    assert code == 303
    reviewed = read_manifest(manifest)[0]
    assert reviewed["reviewed"] and reviewed["mask_reviewed"]
    assert reviewed["group_id"] == "confirmed-product"
    assert reviewed["photo_context"] == "isolated_product"
    assert reviewed["mask_reviewer"] == "test reviewer"
    assert all(value is False for value in reviewed["labels"].values())
    reviewed.update(review_method="ai_visual_review", human_verified=False, human_confirmed=False,
                    mask_review_method="ai_visual_review", mask_human_verified=False, mask_human_confirmed=False)
    write_manifest(manifest, [reviewed])
    code, _, body = request(server, "GET", query)
    assert code == 200 and b"AI-reviewed: 1. Human-verified: 0." in body
    assert b"AI-reviewed reference mask" in body and b"AI review is not human-verified ground truth" in body
    assert b'name="reviewer" type="text" value="" required' in body
    fields["polygon"] = ""
    form, kind = multipart(fields)
    assert request(server, "POST", "/review", form, {"Content-Type": kind})[0] == 303
    confirmed = read_manifest(manifest)[0]
    assert confirmed["review_method"] == confirmed["mask_review_method"] == "human_visual_review"
    assert confirmed["human_verified"] and confirmed["human_confirmed"]
    assert confirmed["mask_human_verified"] and confirmed["mask_human_confirmed"]
    assert confirmed["photo_context_origin"] == "Human visual review submitted through the review form."
