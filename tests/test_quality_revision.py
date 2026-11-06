from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
import pytest

from inspector.images import png_bytes
from inspector.models import InspectionProfile
from inspector.pipeline import classify, inspect_image, measure_image
from inspector.web import render_result
from test_web import multipart, request, server


def by_id(checks):
    return {check.identifier: check for check in checks}


def test_default_home_uses_guarded_profile_and_legacy_is_still_selectable():
    from inspector.web import home
    assert 'name="profile" value="quality-v3-profile"' in home()
    assert 'name="profile" value="Demo White Background"' in home('Demo White Background')


def test_preview_uses_original_and_keeps_overlay_separate(product_bytes):
    result = inspect_image(product_bytes, photo_context="isolated_product")
    document = render_result({"result_json":result.to_json(),"filename":"small.png"})
    main = document.split('class="result-photo"',1)[1].split("<details>",1)[0]
    assert f'/evidence/{result.inspection_id}/original.png' in main
    assert '/overlay.png' not in main
    assert 'Native size: 60 x 80 pixels' in main
    assert 'width="60" height="80"' in main
    assert document.index('View overlay and mask') < document.index(f'/evidence/{result.inspection_id}/overlay.png')


@pytest.mark.parametrize("context",["isolated_product","model_worn","uncertain"])
def test_native_resolution_is_independent_of_blur(context,product_bytes):
    profile = InspectionProfile(min_short_side=200,min_long_side=200,min_sharpness=0,review_band=0)
    checks = by_id(inspect_image(product_bytes,profile,photo_context=context).checks)
    assert len(checks)==12 and checks["low_resolution"].status=="Fail"
    if context=="isolated_product":
        assert checks["blur"].status=="Pass"
    else:
        assert checks["blur"].reason_code=="photo_context"
    assert checks["low_resolution"].reason_code is None


def test_missing_noise_abstains_only_dependent_checks(product_bytes):
    measured=measure_image(product_bytes);measured.quality.pop("noise")
    checks=by_id(classify(measured,InspectionProfile(),photo_context="isolated_product"))
    assert checks["blur"].reason_code=="blur_noise_unavailable"
    assert checks["noise"].reason_code=="insufficient_flat_patches"
    assert checks["low_contrast"].status==checks["low_resolution"].status=="Pass"
    assert all(checks[key].status=="Pass" for key in ("background_color","frame_coverage","underexposure"))


def test_quality_review_does_not_hide_independent_failures(product_bytes):
    measured=measure_image(product_bytes);measured.quality.update(contrast=10,noise=20)
    checks=by_id(classify(measured,InspectionProfile(),photo_context="isolated_product"))
    assert checks["blur"].reason_code=="blur_contrast_confounded"
    assert checks["low_contrast"].status==checks["noise"].status=="Fail"
    profile=InspectionProfile(quality_review_reasons={"noise":"Noise calibration lacks negative references."})
    checks=by_id(classify(measured,profile,photo_context="isolated_product"))
    assert checks["noise"].reason_code=="unsupported_quality_calibration"
    assert checks["low_contrast"].status=="Fail"


def test_noise_acceptance_and_blur_noise_guard_are_distinct(product_bytes):
    measured=measure_image(product_bytes);measured.quality["noise"]=6
    checks=by_id(classify(measured,InspectionProfile(max_noise=4,max_blur_noise=8),photo_context="isolated_product"))
    assert checks["noise"].status=="Fail" and checks["blur"].status=="Pass"
    checks=by_id(classify(measured,InspectionProfile(max_noise=4),photo_context="isolated_product"))
    assert checks["blur"].reason_code=="blur_noise_confounded"


@pytest.mark.parametrize("size",[40,96,511,512,513,1254,1600])
def test_analysis_resize_cap_and_native_size_are_distinct(size,product):
    rgb=cv2.resize(product,(size,size),interpolation=cv2.INTER_NEAREST)
    measured=measure_image(png_bytes(rgb))
    assert measured.rgb.shape[:2]==(size,size)
    assert measured.analysis.shape[:2]==(min(size,512),min(size,512))
    assert np.array_equal(measured.rgb,rgb)


@pytest.mark.parametrize("value",[float('nan'),-1,True,"8"])
def test_blur_noise_guard_validation(value):
    with pytest.raises(ValueError):InspectionProfile(max_blur_noise=value).validate()


@pytest.mark.parametrize("fields",[
    {"sharpness_metric":"unknown"},{"quality_review_bands":{"blur":float('nan')}},
    {"quality_review_bands":{"low_resolution":.1}},{"quality_review_reasons":{"noise":""}},
    {"quality_review_reasons":{"placement":"unsupported"}},
])
def test_new_profile_fields_reject_invalid_values(fields):
    with pytest.raises(ValueError):InspectionProfile.from_dict(fields)


def test_ui_global_band_edit_replaces_per_check_bands(server,monkeypatch,product_bytes):
    import inspector.web as web
    monkeypatch.setattr(web,"available_profiles",lambda **kwargs:{"test":InspectionProfile(quality_review_bands={"blur":.5})})
    identity=str(uuid4())
    form,kind=multipart({"profile":"test","submission_id":identity,"photo_context":"isolated_product","review_band":"0.2"},product_bytes)
    assert request(server,"POST","/inspect",form,{"Content-Type":kind})[0]==303
    code,_,body=request(server,"GET","/report/"+identity+".json")
    profile=json.loads(body)["profile"]
    assert code==200 and profile["quality_review_bands"]=={} and not profile["calibrated"]


@pytest.mark.parametrize("context",["isolated_product","model_worn","uncertain"])
@pytest.mark.parametrize("name",["shoe-96-original.png","shoe-96-gaussian.png","shoe-1254-original.png"])
def test_saved_shoe_regressions_under_all_contexts(context,name):
    from inspector.profiles import load_profile
    source=Path('data/quality-v3/regression')/name
    if not source.is_file():
        pytest.skip('Optional local user-photo regression; private photos are not distributed.')
    profile,_=load_profile(Path('data/quality-v3-profile.json'))
    result=inspect_image(source.read_bytes(),profile,photo_context=context)
    checks=by_id(result.checks)
    assert len(checks)==12 and checks['low_resolution'].status=='Pass'
    if context!='isolated_product':
        assert all(check.reason_code=='photo_context' for key,check in checks.items() if key!='low_resolution')
    elif '1254' in name:
        assert checks['blur'].reason_code=='unsupported_dimensions'
        assert all(checks[key].status=='Pass' for key in ('frame_coverage','placement','margins','background_color'))
    elif 'gaussian' in name:
        assert checks['blur'].status=='Fail'
    else:
        assert checks['blur'].reason_code=='unvalidated_pass_region'


def test_legacy_profile_keeps_original_variance_method():
    source=Path('data/quality-v3/regression/shoe-96-original.png')
    if not source.is_file():
        pytest.skip('Optional local user-photo regression; private photos are not distributed.')
    result=inspect_image(source.read_bytes(),InspectionProfile(),photo_context='isolated_product')
    blur=by_id(result.checks)['blur']
    assert blur.measurement['score']==pytest.approx(3358.795654296875)
    assert blur.measurement['metric']=='laplacian_variance' and blur.status=='Pass'


def test_unsupported_pass_does_not_hide_validated_failure(product_bytes):
    measured=measure_image(product_bytes)
    profile=InspectionProfile(min_sharpness=10000,quality_pass_review_reasons={'blur':'Validation recall is insufficient.'})
    checks=by_id(classify(measured,profile,photo_context='isolated_product'))
    assert checks['blur'].status=='Fail'
    profile.min_sharpness=0
    checks=by_id(classify(measured,profile,photo_context='isolated_product'))
    assert checks['blur'].reason_code=='unvalidated_pass_region'
    assert checks['low_resolution'].status=='Pass'
