from io import BytesIO
import json
from uuid import uuid4

import numpy as np
from PIL import Image
import pytest

from inspector import product_locator
from inspector.images import png_bytes
from inspector.models import InspectionProfile
from inspector.product_locator import inspect_product, polygon_mask, route, select_product
from inspector.storage import load_inspection, list_inspections
from inspector.web import home
from test_web import request, multipart, server


def no_model():
    pytest.fail("A non-clothing route loaded the clothing model")


def test_category_routes_are_explicit_and_segformer_is_clothing_only():
    assert route("general", "auto", "isolated_product") == "foreground"
    assert route("footwear", "auto", "model_worn") == "manual"
    assert route("clothing", "auto", "model_worn") == "segformer"
    assert route("clothing", "auto", "isolated_product") == "foreground"
    assert route("general", "auto", "uncertain") == "manual"
    with pytest.raises(ValueError, match="clothing only"):
        route("general", "segformer", "isolated_product")


def test_general_isolated_product_keeps_supported_dip_passes(monkeypatch, product_bytes):
    monkeypatch.setattr(product_locator, "_model", no_model)
    report = inspect_product(product_bytes, category="footwear", photo_context="isolated_product")
    assert report.segmentation["product_locator"]["resolved_mode"] == "foreground"
    assert report.segmentation["product_locator"]["status"] == "candidate"
    assert report.status == "Pass" and all(check.status == "Pass" for check in report.checks)
    q = report.segmentation["whole_photo"]["diagnostics"]
    assert q["contrast"] > 0 and q["noise"] is not None
    assert "dark_fraction" in q and "bright_fraction" in q
    assert q["threshold_status"] == "measurement_only_no_whole_photo_quality_calibration"


def test_unresolved_target_does_not_stop_resolution_or_whole_photo(monkeypatch, product_bytes):
    monkeypatch.setattr(product_locator, "_model", no_model)
    profile=InspectionProfile(min_short_side=200,min_long_side=200)
    report=inspect_product(product_bytes,profile,category="general",photo_context="uncertain")
    assert report.status == "Fail" and report.checks[0].status == "Fail"
    assert all(c.reason_code == "product_target_unresolved" for c in report.checks[1:])
    assert report.segmentation["product_diagnostics"] is None
    assert report.segmentation["whole_photo"]["diagnostics"]["contrast"] > 0


def test_multiple_nonclothing_objects_and_manual_choice_preserve_scope(monkeypatch, product):
    monkeypatch.setattr(product_locator, "_model", no_model)
    rgb=np.concatenate([product,product],axis=1)
    parent=inspect_product(png_bytes(rgb),category="general",mode="foreground",photo_context="isolated_product")
    assert parent.segmentation["product_locator"]["status"] == "ambiguous"
    record={"result_json":parent.to_json(),"original_png":parent.original_png,"mask_png":parent.mask_png,"overlay_png":parent.overlay_png}
    before=record["result_json"]
    child=select_product(record,polygon=[[15,12],[44,12],[44,67],[15,67]])
    assert record["result_json"] == before
    assert child.inspection_id != parent.inspection_id
    assert child.checks[0].to_dict() == parent.checks[0].to_dict()
    assert child.segmentation["whole_photo"] == parent.segmentation["whole_photo"]
    assert child.segmentation["product_locator"]["selection_method"] == "polygon"
    assert child.segmentation["product_locator"]["inference_rerun"] is False
    assert not np.asarray(Image.open(BytesIO(child.mask_png)))[:,60:].any()
    by_id={c.identifier:c for c in child.checks}
    assert by_id["frame_coverage"].status == "Pass"
    assert by_id["placement"].status == "Fail"
    assert by_id["margins"].status == "Pass"
    assert by_id["blur"].status == "Manual Review"
    assert child.status == "Fail"


@pytest.mark.parametrize("points", [None, [], [[1,1],[4,4]], [[1,1],[1,1],[1,1]],
    [[-1,1],[5,1],[5,5]], [[float('nan'),1],[5,1],[5,5]], [[True,1],[5,1],[5,5]],
    [[0,0],[59,0],[59,79],[0,79]]])
def test_invalid_or_full_photo_outline_rejected(points):
    with pytest.raises(ValueError): polygon_mask(points,(60,80))


def test_http_generic_marking_and_incompatible_model_guard(server, monkeypatch, product_bytes):
    monkeypatch.setattr(product_locator, "_model", no_model)
    identity=str(uuid4())
    fields={"submission_id":identity,"profile":"Demo White Background","photo_context":"uncertain",
            "product_category":"general","product_locator":"auto"}
    form,kind=multipart(fields,product_bytes)
    assert request(server,"POST","/inspect",form,{"Content-Type":kind})[0] == 303
    parent=load_inspection(identity,server.database)
    document=request(server,"GET",f"/inspection/{identity}")[2]
    assert b"Product locator" in document and b"Shirt locator" not in document
    assert b'id="whole-photo-checks"' in document
    assert b'action="/mark-product" enctype="multipart/form-data"' in document
    child_id=str(uuid4())
    form,kind=multipart({"submission_id":child_id,"source_inspection_id":identity,
        "polygon":json.dumps([[15,12],[44,12],[44,67],[15,67]])})
    assert request(server,"POST","/mark-product",form,{"Content-Type":kind})[0] == 303
    assert load_inspection(identity,server.database)["result_json"] == parent["result_json"]
    fields.update(submission_id=str(uuid4()),product_locator="segformer")
    form,kind=multipart(fields,product_bytes)
    assert request(server,"POST","/inspect",form,{"Content-Type":kind})[0] == 400
    assert len(list_inspections(server.database)) == 2


def test_current_ui_uses_category_and_product_words():
    document=home()
    assert 'name="product_category"' in document and 'name="product_locator"' in document
    assert 'Shirt locator' not in document and 'Experimental shirt locator' not in document
    assert 'disabled hidden>Clothing SegFormer' in document


def test_generic_component_choice_uses_product_route(server, product):
    identity=str(uuid4())
    image=png_bytes(np.concatenate([product,product],axis=1))
    form,kind=multipart({"submission_id":identity,"profile":"Demo White Background",
        "photo_context":"isolated_product","product_category":"general","product_locator":"foreground"},image)
    assert request(server,"POST","/inspect",form,{"Content-Type":kind})[0] == 303
    page=request(server,"GET",f"/inspection/{identity}")[2]
    assert b'action="/select-product" enctype="multipart/form-data"' in page
    assert b'name="product_target"' in page
    child_id=str(uuid4())
    form,kind=multipart({"submission_id":child_id,"source_inspection_id":identity,"product_target":"2"})
    assert request(server,"POST","/select-product",form,{"Content-Type":kind})[0] == 303
    child=json.loads(load_inspection(child_id,server.database)["result_json"])
    assert child["segmentation"]["product_locator"]["status"] == "selected"
    assert child["segmentation"]["product_locator"]["selected_target"] == 2


def test_person_background_review_is_separate_from_product_geometry(product_bytes):
    report=inspect_product(product_bytes,mode="manual",photo_context="model_worn")
    record={"result_json":report.to_json(),"original_png":report.original_png,"mask_png":report.mask_png,"overlay_png":report.overlay_png}
    child=select_product(record,polygon=[[15,12],[44,12],[44,67],[15,67]])
    checks={c.identifier:c for c in child.checks}
    assert checks["background_uniformity"].reason_code == "scene_background_unseparated"
    assert checks["frame_coverage"].status == "Pass" and checks["low_resolution"].status == "Pass"
