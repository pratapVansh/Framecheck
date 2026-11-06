import copy
import json
import urllib.error

import pytest

from inspector import narration


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch,tmp_path):
    monkeypatch.setattr(narration,'ENV_FILE',tmp_path/'.env',raising=False)
    narration._explain.cache_clear()
    yield
    narration._explain.cache_clear()


@pytest.fixture
def report():
    return {'status':'Fail','checks':[
        {'identifier':'photo.noise','status':'Fail','explanation':'The noise estimate exceeds the pilot limit.'},
        {'identifier':'product-1.edge','status':'Pass','explanation':'No product contact with the photo edge was found.'}]}


def response_for(payload,finish='stop',usage=None):
    raw=json.dumps({'choices':[{'finish_reason':finish,'message':{'content':json.dumps(payload)}}],'usage':usage}).encode()
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return raw
    return Response()


def test_no_key_never_calls_api_and_retains_all_findings(monkeypatch,report):
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    monkeypatch.setattr(narration.urllib.request,'urlopen',lambda *a,**k:pytest.fail('No key must not call Groq'))
    output=narration.explain(report)
    assert output['source']=='local' and len(output['items'])==2


def test_valid_response_is_server_side_and_cannot_change_verdict(monkeypatch,report):
    secret='test-only-key-not-a-real-secret'
    monkeypatch.setenv('GROQ_API_KEY',secret)
    original=copy.deepcopy(report)
    def transport(request,timeout):
        body=json.loads(request.data)
        assert timeout==4 and body['response_format']['json_schema']['strict'] is True
        assert body['reasoning_effort']=='low' and 512<=body['max_completion_tokens']<=4096
        assert secret not in request.data.decode() and 'filename' not in request.data.decode()
        assert request.get_header('Authorization')=='Bearer '+secret
        return response_for({'items':list(reversed(narration.approved_wording(report)))})
    monkeypatch.setattr(narration.urllib.request,'urlopen',transport)
    result=narration.explain(report)
    assert result['source']=='groq_validated'
    assert report==original and report['status']=='Fail'
    assert secret not in json.dumps(result)


@pytest.mark.parametrize('bad',['unknown_id','new_claim','swapped_claim','verdict','missing','duplicate','truncated'])
def test_unsupported_claims_or_invalid_contract_fall_back(monkeypatch,report,bad):
    monkeypatch.setenv('GROQ_API_KEY','test-key')
    items=narration.approved_wording(report)
    payload={'items':copy.deepcopy(items)}
    if bad=='unknown_id':payload['items'][0]['finding_id']='invented'
    if bad=='new_claim':payload['items'][0]['statement']='The camera shook and the listing will be rejected.'
    if bad=='swapped_claim':payload['items'][0]['statement']=items[1]['statement']
    if bad=='verdict':payload['verdict']='Pass'
    if bad=='missing':payload['items']=payload['items'][:1]
    if bad=='duplicate':payload['items'].append(items[0])
    monkeypatch.setattr(narration.urllib.request,'urlopen',lambda *a,**k:response_for(payload,'length' if bad=='truncated' else 'stop'))
    output=narration.explain(report)
    assert output['source']=='local' and output['items']==items


def test_timeout_fallback_never_exposes_key(monkeypatch,report):
    monkeypatch.setenv('GROQ_API_KEY','never-echo-this-key')
    def timeout(*args,**kwargs):raise TimeoutError('never-echo-this-key')
    monkeypatch.setattr(narration.urllib.request,'urlopen',timeout)
    result=narration.explain(report)
    assert result['source']=='local' and 'never-echo-this-key' not in json.dumps(result)


def test_review_advice_does_not_assert_a_confirmed_defect():
    report={'checks':[{'identifier':'photo.blur','status':'Manual Review',
        'reason_code':'calibration_boundary','explanation':'Sharpness is uncertain.'}]}
    item=narration.approved_wording(report)[0]
    assert item['fix'].startswith('This check is uncertain.') and 'If the issue is visible:' in item['fix']


def test_local_env_loads_only_groq_settings(tmp_path,monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    monkeypatch.delenv('GROQ_MODEL',raising=False)
    monkeypatch.delenv('FRAMECHECK_UNRELATED_TEST',raising=False)
    path=tmp_path/'.env'
    path.write_text('\ufeff# Local settings\nGROQ_API_KEY = "test-local-key"\nGROQ_MODEL=example-model\nFRAMECHECK_UNRELATED_TEST=ignored\n',encoding='utf-8')
    narration.load_groq_env(path)
    assert narration.os.environ['GROQ_API_KEY']=='test-local-key'
    assert narration.os.environ['GROQ_MODEL']=='example-model'
    assert 'FRAMECHECK_UNRELATED_TEST' not in narration.os.environ


def test_local_env_preserves_process_environment(tmp_path,monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','process-key')
    path=tmp_path/'.env';path.write_text('GROQ_API_KEY=file-key\n',encoding='utf-8')
    narration.load_groq_env(path)
    assert narration.os.environ['GROQ_API_KEY']=='process-key'


def test_missing_or_blank_local_env_keeps_fallback(tmp_path,monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    path=tmp_path/'.env'
    narration.load_groq_env(path)
    assert 'GROQ_API_KEY' not in narration.os.environ
    path.write_text('GROQ_API_KEY=\n',encoding='utf-8')
    narration.load_groq_env(path)
    assert not narration.os.environ['GROQ_API_KEY']


@pytest.mark.parametrize('initial_env',[None,''])
def test_saved_key_activates_without_restart_and_identical_refresh_is_cached(monkeypatch,report,initial_env):
    if initial_env is None:monkeypatch.delenv('GROQ_API_KEY',raising=False)
    else:monkeypatch.setenv('GROQ_API_KEY',initial_env)
    narration.ENV_FILE.write_text('GROQ_API_KEY=\n',encoding='utf-8')
    requests=[]
    def transport(request,timeout):
        requests.append(request)
        assert request.get_header('Authorization')=='Bearer saved-test-key'
        body=json.loads(request.data)
        return response_for({'items':json.loads(body['messages'][1]['content'])['approved_items']})
    monkeypatch.setattr(narration.urllib.request,'urlopen',transport)
    assert narration.explain(report)['reason']=='not_configured'
    narration.ENV_FILE.write_text('GROQ_API_KEY="saved-test-key"\n',encoding='utf-8')
    assert narration.explain(report)['source']=='groq_validated'
    assert narration.explain(report)['source']=='groq_validated'
    assert len(requests)==1
    assert narration.os.environ.get('GROQ_API_KEY')==initial_env


def test_key_rotation_invalidates_auth_failure_and_process_setting_takes_precedence(monkeypatch,report):
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    narration.ENV_FILE.write_text('GROQ_API_KEY=old-test-key\n',encoding='utf-8')
    requests=[]
    def transport(request,timeout):
        requests.append(request)
        if request.get_header('Authorization')=='Bearer old-test-key':
            raise urllib.error.HTTPError(request.full_url,401,'Rejected',{},None)
        return response_for({'items':narration.approved_wording(report)})
    monkeypatch.setattr(narration.urllib.request,'urlopen',transport)
    assert narration.explain(report)['reason']=='api_http_401'
    narration.ENV_FILE.write_text('GROQ_API_KEY=new-test-key\n',encoding='utf-8')
    assert narration.explain(report)['source']=='groq_validated'
    assert requests[-1].get_header('Authorization')=='Bearer new-test-key'
    monkeypatch.setenv('GROQ_API_KEY','process-test-key')
    assert narration.explain(report)['source']=='groq_validated'
    assert requests[-1].get_header('Authorization')=='Bearer process-test-key'
    assert len(requests)==3


def test_network_access_failure_has_distinct_sanitized_reason(monkeypatch,report):
    monkeypatch.setenv('GROQ_API_KEY','do-not-echo-key')
    def transport(*args,**kwargs):raise urllib.error.URLError(PermissionError(10013,'do-not-echo-key'))
    monkeypatch.setattr(narration.urllib.request,'urlopen',transport)
    result=narration.explain(report)
    assert result['reason']=='api_network_access_denied'
    assert result['items']==narration.approved_wording(report)
    assert 'do-not-echo-key' not in json.dumps(result)


def test_provider_usage_contains_only_nonnegative_integer_counts(monkeypatch,report):
    monkeypatch.setenv('GROQ_API_KEY','do-not-echo-key')
    def transport(*args,**kwargs):
        return response_for({'items':narration.approved_wording(report)},usage={
            'prompt_tokens':42,'completion_tokens':-1,'total_tokens':'do-not-echo-key','other':'ignored'})
    monkeypatch.setattr(narration.urllib.request,'urlopen',transport)
    result=narration.explain(report)
    assert result['source']=='groq_validated' and result['usage']=={'prompt_tokens':42}
    assert 'do-not-echo-key' not in json.dumps(result)
