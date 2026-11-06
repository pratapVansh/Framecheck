from dataclasses import replace

import cv2
import numpy as np

from inspector.dip_alternative import edge_measurements, classify_alternative
from inspector.images import png_bytes
from inspector.models import InspectionProfile, Status
from inspector.pipeline import measure_image


def test_tenengrad_decreases_with_blur_and_stages_are_preserved(product):
    clear = measure_image(png_bytes(product))
    blurred = measure_image(png_bytes(cv2.GaussianBlur(product,(0,0),2)))
    # Compare the same safe interior: changing segmentation can inflate edge energy.
    roi = np.zeros(product.shape[:2], bool)
    roi[22:58, 21:39] = True
    clear.segmentation.mask = roi
    blurred.segmentation.mask = roi
    assert edge_measurements(clear)["tenengrad"] > edge_measurements(blurred)["tenengrad"]
    assert set(clear.segmentation.stages)=={"color_distance","closed","components"}
    assert all(v.shape==product.shape[:2] for v in clear.segmentation.stages.values())


def test_alternative_never_bypasses_scope_dimensions_or_resolution(product):
    profile = InspectionProfile()
    small = measure_image(png_bytes(cv2.resize(product,(30,40))))
    checks,_ = classify_alternative(small,profile,photo_context="model_worn")
    assert checks[0].status==Status.FAIL
    assert all(c.status==Status.REVIEW and c.reason_code=="photo_context" for c in checks[1:])
    measured = measure_image(png_bytes(product))
    checks,_=classify_alternative(measured,replace(profile,calibration_short_min=61),photo_context="isolated_product")
    assert all(c.status==Status.REVIEW and c.reason_code=="unsupported_dimensions" for c in checks if c.identifier in {'blur','noise','low_contrast','underexposure','overexposure'})


def test_unstable_mask_stays_uncertain_and_cropping_never_fails(product):
    measured=measure_image(png_bytes(product))
    measured.segmentation.reliable=False
    measured.segmentation.diagnostics['stability_iou']=0.1
    checks,d=classify_alternative(measured,InspectionProfile(),photo_context='isolated_product')
    assert not d['object_eligible']
    assert all(c.status==Status.REVIEW for c in checks[1:])
    assert next(c for c in checks if c.identifier=='possible_cropping').status!=Status.FAIL


def test_background_shortfall_does_not_suppress_independent_product_checks(product):
    measured = measure_image(png_bytes(product))
    measured.background = {'pixels':0}
    checks,d = classify_alternative(measured,InspectionProfile(),photo_context='isolated_product')
    assert d['object_eligible'] and not d['background_eligible']
    assert next(c for c in checks if c.identifier=='frame_coverage').status==Status.PASS
    assert all(c.status==Status.REVIEW and c.reason_code=='insufficient_background' for c in checks if c.identifier in {'background_color','background_uniformity'})


def test_uniform_profile_still_disables_only_the_white_rule(product):
    measured = measure_image(png_bytes(product))
    measured.background = {'pixels':0}
    checks,_ = classify_alternative(measured,InspectionProfile(require_white=False),photo_context='isolated_product')
    white = next(c for c in checks if c.identifier=='background_color')
    assert white.status==Status.DISABLED and white.reason_code is None
    assert next(c for c in checks if c.identifier=='background_uniformity').status==Status.REVIEW
