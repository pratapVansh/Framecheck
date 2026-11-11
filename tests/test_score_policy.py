"""Provisional score-policy contracts, not threshold calibration evidence."""
import copy
import json

import pytest

from inspector.auto_inspect import inspect_auto, policy
from inspector.images import png_bytes
from inspector.modern_ui import render_result
from inspector.scoring import CRITICAL_CHECKS, score_photo, score_statement
from inspector.storage import load_inspection, save_inspection
from test_one_click import normal_image, unit_policy
from test_score_intent import perfect_checks
from test_web import server, request, multipart


BOUNDARIES = {
    74:['photo.contrast','product.detail','product.clipping','photo.group_size','photo.group_placement','photo.group_margins'],
    75:['photo.contrast','product.detail','product.contrast','product.clipping','photo.group_size','photo.group_placement'],
    76:['photo.contrast','product.detail','product.contrast','product.clipping','photo.group_placement','photo.group_margins'],
}


def assessment(failures=(), *, threshold=75):
    checks=perfect_checks()
    for check in checks:
        key=check['identifier'].replace('product-1.','product.')
        if key in failures:check['status']='Fail'
    return score_photo(checks,[{'number':1}],'Fail' if failures else 'Pass',
                       verdict_policy={'pass_threshold':threshold})


@pytest.mark.parametrize('points,verdict',[(74,'Needs Improvement'),(75,'Pass'),(76,'Pass')])
def test_threshold_boundary_uses_existing_deductions(points,verdict):
    result=assessment(BOUNDARIES[points])
    assert result['score']==points and result['verdict']==verdict
    assert result['confirmed_deductions']==100-points and result['uncertainty_withheld']==0
    assert result['possible_range']==[points,points]
    assert not result['incomplete_checks'] and not result['critical_failures']


def test_threshold_is_configurable_without_changing_score_math():
    assert policy()['score_verdict']['pass_threshold']==75
    lower=assessment(BOUNDARIES[76],threshold=76)
    higher=assessment(BOUNDARIES[76],threshold=77)
    assert lower['verdict']=='Pass' and higher['verdict']=='Needs Improvement'
    assert lower['components']==higher['components']
    assert lower['score']==higher['score']==76


@pytest.mark.parametrize('threshold',[None,True,'75',-1,101,10**400,float('nan'),float('inf')])
def test_invalid_threshold_requires_review(threshold):
    result=assessment(threshold=threshold)
    assert result['score']==100 and result['verdict']=='Needs Review'
    assert result['verdict_reason']=='invalid_score_policy'
    assert 'threshold setting is invalid' in score_statement(result)


@pytest.mark.parametrize('component',sorted(CRITICAL_CHECKS))
def test_confirmed_critical_defect_blocks_a_high_score(component):
    result=assessment([component])
    assert result['score']>=75 and result['verdict']=='Needs Improvement'
    assert result['verdict_reason']=='critical_defect' and len(result['critical_failures'])==1
    assert 'critical problem must be fixed' in score_statement(result)


@pytest.mark.parametrize('state',['Manual Review','Disabled','missing','excluded'])
def test_incomplete_required_check_is_not_a_deduction_or_a_pass(state):
    checks=perfect_checks()
    target=next(c for c in checks if c['identifier']=='photo.coverage')
    if state=='missing':checks.remove(target)
    elif state=='excluded':target['required']=False
    else:target['status']=state
    result=score_photo(checks,[{'number':1}],'Pass')
    assert result['score']==90 and result['verdict']=='Needs Review'
    assert result['confirmed_deductions']==0 and result['uncertainty_withheld']==10
    assert result['possible_range']==[90,100]
    assert 'photo.coverage' in result['incomplete_checks']


def test_partial_product_assessment_stays_incomplete_after_another_product_fails():
    checks=perfect_checks((1,2))
    next(c for c in checks if c['identifier']=='product-1.contrast')['status']='Fail'
    next(c for c in checks if c['identifier']=='product-2.contrast')['status']='Manual Review'
    result=score_photo(checks,[{'number':1},{'number':2}],'Fail')
    assert result['score']==98 and result['confirmed_deductions']==2
    assert result['uncertainty_withheld']==0 and result['verdict']=='Needs Review'
    assert result['incomplete_checks']==['product-2.contrast']


def test_critical_failure_wins_over_incomplete_evidence_and_exclusions_stay_advisory():
    checks=perfect_checks()
    next(c for c in checks if c['identifier']=='photo.resolution')['status']='Fail'
    next(c for c in checks if c['identifier']=='photo.coverage')['status']='Manual Review'
    result=score_photo(checks,[{'number':1}],'Fail')
    assert result['verdict']=='Needs Improvement' and result['incomplete_checks']
    checks=perfect_checks((1,2))
    next(c for c in checks if c['identifier']=='product-2.noise')['status']='Fail'
    next(c for c in checks if c['identifier']=='product-2.blur')['status']='Manual Review'
    result=score_photo(checks,[{'number':1},{'number':2,'intended':False}],'Pass')
    assert result['score']==100 and result['verdict']=='Pass'
    assert not result['critical_failures']


def test_unknown_required_failure_cannot_silently_pass():
    checks=perfect_checks()+[{'identifier':'photo.new_check','status':'Fail'}]
    result=score_photo(checks,[{'number':1}],'Fail')
    assert result['score']==100 and result['verdict']=='Needs Review'


def test_policy_verdict_roundtrips_storage_and_passing_photos_have_suggestions(normal_image,unit_policy,tmp_path):
    result=inspect_auto(png_bytes(normal_image),calibration=copy.deepcopy(unit_policy))
    for check in result.checks:
        key=check.identifier.replace('product-1.','product.')
        if key in BOUNDARIES[75]:check.status='Fail'
    assert result.status=='Pass' and result.score()['score']==75
    database=tmp_path/'policy.sqlite3'
    save_inspection(result,'boundary.png',database)
    saved=load_inspection(result.inspection_id,database)
    payload=json.loads(saved['result_json'])
    assert saved['status']==payload['status']==payload['quality_score']['verdict']=='Pass'
    assert payload['quality_score']['check_verdict']=='Fail'
    assert 'provisional Pass threshold' in score_statement(payload['quality_score'])
    html=render_result(saved)
    assert 'Make it even better' in html and 'Improve visible detail' in html
    assert 'Provisional Pass threshold: 75/100' in html
    assert load_inspection(result.inspection_id,database)['result_json']==saved['result_json']
    clear=inspect_auto(png_bytes(normal_image),calibration=unit_policy)
    clear_html=render_result({'result_json':clear.to_json(),'filename':'clear.png'})
    overview=clear_html.split('<details class="review-details"',1)[0]
    assert 'Your photo looks good.' in overview
    assert 'class="action-item"' not in overview


def test_invalid_policy_can_be_saved_and_configuration_is_frozen_per_inspection(normal_image,unit_policy):
    unit_policy['score_verdict']={'pass_threshold':float('nan')}
    result=inspect_auto(png_bytes(normal_image),calibration=unit_policy)
    assert result.status=='Needs Review'
    assert json.loads(result.to_json())['profile']['score_verdict']=={'pass_threshold':None}
    assert 'Invalid setting' in render_result({'result_json':result.to_json(),'filename':'fixture.png'})
    unit_policy['score_verdict']={'pass_threshold':75}
    assert result.status=='Needs Review'


@pytest.mark.parametrize('threshold,verdict',[(75,'Pass'),(95,'Needs Improvement')])
def test_http_and_history_share_the_configured_backend_verdict(server,monkeypatch,normal_image,unit_policy,threshold,verdict):
    from uuid import uuid4
    from inspector import auto_inspect
    unit_policy['score_verdict']={'pass_threshold':threshold}
    monkeypatch.setattr(auto_inspect,'policy',lambda:unit_policy)
    original=auto_inspect.photo_findings
    def findings(*args):
        checks,measurements=original(*args)
        check=next(c for c in checks if c.identifier=='photo.contrast')
        check.status='Fail';check.explanation='Controlled test-only noncritical failure.'
        return checks,measurements
    monkeypatch.setattr(auto_inspect,'photo_findings',findings)
    identity=str(uuid4())
    body,kind=multipart({'one_click':'yes','submission_id':identity},png_bytes(normal_image))
    assert request(server,'POST','/inspect',body,{'Content-Type':kind})[0]==303
    code,_,raw=request(server,'GET',f'/report/{identity}.json')
    assert code==200
    result=json.loads(raw)
    assert result['quality_score']['score']==92
    assert result['status']==result['quality_score']['verdict']==verdict
    assert load_inspection(identity,server.database)['status']==verdict
    assert verdict.encode() in request(server,'GET','/history')[2]
    unit_policy['score_verdict']={'pass_threshold':100}
    assert request(server,'GET',f'/report/{identity}.json')[2]==raw
