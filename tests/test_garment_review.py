import base64
import http.client
from io import BytesIO
import json
import socket
import threading

import pytest
from PIL import Image

from scripts.garment_pilot import digest
from scripts.garment_review import current, make_server, save_review


def encoded_mask(size=(16, 12), value=None):
    image = Image.new("L", size, 0 if value is None else value)
    if value is None:
        for x in range(4, 12):
            for y in range(3, 10):
                image.putpixel((x, y), 255)
    output = BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


@pytest.fixture
def review_case(tmp_path):
    image = tmp_path / "original.png"
    Image.new("RGB", (16, 12), "gray").save(image)
    folder = tmp_path / "references" / "synthetic"
    folder.mkdir(parents=True)
    Image.new("L", (16, 12), 0).save(folder / "proposal.png")
    case = {"id": "synthetic", "image": str(image), "image_sha256": digest(image),
            "native_size": [16, 12], "target": "Synthetic fixture only", "seed": "blank",
            "proposal_sha256": digest(folder / "proposal.png")}
    (tmp_path / "review.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    return tmp_path, case


def payload(**changes):
    return {"revision": 0, "approved": False, "reviewer": "Synthetic test reviewer",
            "confirmed": False, "notes": "Test fixture, never evaluation truth", "seed": "blank",
            "mask": encoded_mask(), **changes}


def test_no_approval_without_human_confirmation_and_nonempty_mask(review_case):
    root, case = review_case
    assert current(root, case)[0]["approved"] is False
    for changes in ({"confirmed": False}, {"confirmed": True, "reviewer": ""},
                    {"confirmed": True, "mask": encoded_mask(value=0)},
                    {"confirmed": True, "mask": encoded_mask(value=255)}):
        with pytest.raises(ValueError):
            save_review(root, case, payload(approved=True, **changes))
    assert current(root, case)[0]["revision"] == 0


def test_native_binary_masks_and_unchanged_sources_required(review_case):
    root, case = review_case
    for mask in (encoded_mask(size=(8, 6), value=0), encoded_mask(value=128)):
        with pytest.raises(ValueError):
            save_review(root, case, payload(mask=mask))
    Image.new("RGB", (16, 12), "red").save(case["image"])
    with pytest.raises(ValueError, match="Original"):
        save_review(root, case, payload())


def test_revision_history_stale_tab_and_edit_invalidation(review_case):
    root, case = review_case
    first = save_review(root, case, payload(approved=True, confirmed=True))
    assert first["approved"] and not first["independent_blind_reference"]
    first_path = current(root, case)[1]
    first_hash = digest(first_path)
    with pytest.raises(ValueError, match="Stale"):
        save_review(root, case, payload())
    second = save_review(root, case, payload(revision=1))
    assert second["revision"] == 2 and not second["approved"]
    assert digest(first_path) == first_hash
    # Interrupted saves do not destroy prior versions or block the next save.
    (root / "references/synthetic/000003").mkdir()
    assert save_review(root, case, payload(revision=2))["revision"] == 4
    current(root, case)[1].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed"):
        current(root, case)


def test_loopback_http_saves_only_from_editor_origin(review_case):
    root, _ = review_case
    server = make_server(root, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    idle = socket.create_connection(("127.0.0.1", server.server_port), timeout=5)
    try:
        connection.request("GET", "/api")
        response = connection.getresponse()
        assert response.status == 200
        assert not json.loads(response.read())["cases"][0]["review"]["approved"]
        body = json.dumps(payload())
        connection.request("POST", "/save/synthetic", body, {"Content-Type": "application/json", "Origin": "https://example.org"})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("POST", "/save/synthetic", body, {"Content-Type": "application/json", "Origin": origin})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["revision"] == 1
        connection.request("GET", "/assets/synthetic/../../original")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
    finally:
        idle.close()
        connection.close()
        server.shutdown()
        thread.join()
        server.server_close()
