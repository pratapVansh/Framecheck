from dataclasses import replace
from io import BytesIO
import json

import cv2
import numpy as np
from PIL import Image
import pytest

from inspector.images import ImageInputError, decode_image, png_bytes
from inspector.models import CheckResult, DEFECTS, InspectionProfile, Status, aggregate
from inspector.pipeline import inspect_image
from scripts.generate import transform


def checks(result):
    return {check.identifier: check for check in result.checks}


def test_clear_product_and_evidence(product_bytes):
    result = inspect_image(product_bytes, photo_context="isolated_product")
    assert result.status == Status.PASS
    assert set(checks(result)) == set(DEFECTS)
    assert result.quality_status == result.compliance_status == Status.PASS
    assert result.segmentation["bbox"] == [15, 12, 30, 56]
    mask, _ = decode_image(result.mask_png)
    assert mask.shape == (80, 60, 3)
    assert np.all(mask[20:60, 20:40] == 255)
    assert np.all(mask[:5] == 0)
    assert "uncalibrated" in " ".join(result.notes)
    assert json.loads(result.to_json())["status"] == "Pass"


def test_deterministic_masks(product_bytes):
    first, second = inspect_image(product_bytes, photo_context="isolated_product"), inspect_image(product_bytes, photo_context="isolated_product")
    assert first.mask_png == second.mask_png
    assert [c.to_dict() for c in first.checks] == [c.to_dict() for c in second.checks]


def test_blur_and_resolution_are_independent(product):
    blurred = cv2.GaussianBlur(product, (0, 0), 2)
    result = inspect_image(png_bytes(blurred), photo_context="isolated_product")
    assert checks(result)["blur"].status in {Status.FAIL, Status.REVIEW}
    tiny = cv2.resize(blurred, (20, 25))
    result = inspect_image(png_bytes(tiny), photo_context="isolated_product")
    assert result.status == Status.FAIL
    assert checks(result)["low_resolution"].status == Status.FAIL
    assert len(result.checks) == 12


@pytest.mark.parametrize("defect", ["low_contrast", "noise", "underexposure", "overexposure"])
def test_quality_transforms_report_multiple_issues(product, defect):
    mask = np.zeros((80, 60), bool)
    mask[12:68, 15:45] = True
    variant, _, _ = transform(product, defect, 2, 42, mask)
    result = inspect_image(png_bytes(variant), photo_context="isolated_product")
    assert checks(result)[defect].status in {Status.FAIL, Status.REVIEW}
    assert result.status != Status.PASS
    assert len(result.checks) == 12


def test_profile_changes_only_color_compliance(product):
    mask = np.zeros((80, 60), bool)
    mask[12:68, 15:45] = True
    product[~mask] = [210, 220, 235]
    white = inspect_image(png_bytes(product), photo_context="isolated_product")
    uniform = inspect_image(png_bytes(product), InspectionProfile(name="Uniform", require_white=False), photo_context="isolated_product")
    assert checks(white)["background_color"].status == Status.FAIL
    assert checks(uniform)["background_color"].status == Status.DISABLED
    assert uniform.compliance_status == Status.PASS
    assert white.quality_status == uniform.quality_status


def test_no_foreground_and_white_foreground_do_not_silently_pass():
    result = inspect_image(png_bytes(np.full((80, 60, 3), 255, np.uint8)), photo_context="isolated_product")
    assert result.status == Status.REVIEW
    assert not result.segmentation["reliable"]
    assert checks(result)["low_resolution"].status == Status.PASS


def test_edge_contact_is_advisory(product):
    edge = np.full_like(product, 255)
    edge[12:68, :30] = product[12:68, 15:45]
    result = inspect_image(png_bytes(edge), photo_context="isolated_product")
    assert checks(result)["possible_cropping"].status == Status.REVIEW
    assert checks(result)["possible_cropping"].measurement["score"] > 0
    assert result.status == Status.FAIL  # Definite placement/margin failure still takes precedence.


def test_large_image_is_outside_quality_calibration(product):
    result = inspect_image(png_bytes(cv2.resize(product, (600, 800))), photo_context="isolated_product")
    assert checks(result)["low_resolution"].status == Status.PASS
    assert all(check.status == Status.REVIEW for check in result.checks if check.group == "quality" and check.identifier != "low_resolution")


@pytest.mark.parametrize("invalid", [b"", b"not an image", b"x" * (10 * 1024 * 1024 + 1)], ids=["empty", "malformed", "oversized"])
def test_bad_uploads(invalid):
    with pytest.raises(ImageInputError):
        decode_image(invalid)


def test_grayscale_and_transparency():
    output = BytesIO()
    Image.new("RGBA", (60, 80), (0, 0, 0, 0)).save(output, format="PNG")
    image, notes = decode_image(output.getvalue())
    assert np.all(image == 255)
    assert "Transparency" in notes[0]
    output = BytesIO()
    Image.new("L", (60, 80), 100).save(output, format="PNG")
    image, notes = decode_image(output.getvalue())
    assert image.shape == (80, 60, 3) and "Grayscale" in notes[0]


def test_orientation_and_format_validation():
    image = Image.new("RGB", (80, 60), "white")
    exif = Image.Exif()
    exif[274] = 6
    output = BytesIO()
    image.save(output, format="JPEG", exif=exif)
    decoded, _ = decode_image(output.getvalue())
    assert decoded.shape[:2] == (80, 60)
    output = BytesIO()
    image.save(output, format="GIF")
    with pytest.raises(ImageInputError):
        decode_image(output.getvalue())


@pytest.mark.parametrize("updates", [{"min_coverage": 0.9, "max_coverage": 0.5}, {"min_noise": 2}, {"max_noise": float("nan")}, {"max_noise": "8"}, {"min_sharpness": True}, {"require_white": "false"}, {"min_short_side": 80.5}, {"min_margin": 0.3}, {"calibrated": True}])
def test_profile_validation(updates):
    with pytest.raises(ValueError):
        InspectionProfile.from_dict({**InspectionProfile().to_dict(), **updates})


def test_decision_precedence():
    def check(status):
        return CheckResult("example", "quality", status, {}, {}, "test")
    assert aggregate([check(Status.REVIEW), check(Status.FAIL)]) == Status.FAIL
    assert aggregate([check(Status.REVIEW), check(Status.PASS)]) == Status.REVIEW
    assert aggregate([check(Status.DISABLED)]) == Status.REVIEW
    assert aggregate([]) == Status.REVIEW


def test_one_pixel_geometry_boundary(product_bytes):
    result = inspect_image(product_bytes, InspectionProfile(min_coverage=0.7), photo_context="isolated_product")
    assert checks(result)["frame_coverage"].status == Status.REVIEW
