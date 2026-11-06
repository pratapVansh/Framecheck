import json
import urllib.error

import pytest

from scripts.check_groq_key import check_key


def test_verified_key_uses_one_get_and_no_generation(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','test-only-secret')
    requests=[]
    def transport(request,timeout):
        requests.append(request)
        assert request.full_url=='https://api.groq.com/openai/v1/models'
        assert request.get_method()=='GET' and request.data is None and timeout==8
        assert request.get_header('Authorization')=='Bearer test-only-secret'
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,limit):return b'{"data":[]}'
        return Response()
    monkeypatch.setattr('scripts.check_groq_key.urllib.request.urlopen',transport)
    result=check_key()
    assert result['key_valid'] is True and len(requests)==1
    assert result['generated_tokens']==result['generation_requests']==0
    assert 'test-only-secret' not in json.dumps(result)


@pytest.mark.parametrize('code,valid,status',[(401,False,'rejected'),(403,None,'unverified')])
def test_http_rejection_is_distinct_from_access_failure(monkeypatch,code,valid,status):
    monkeypatch.setenv('GROQ_API_KEY','test-only-secret')
    def transport(*args,**kwargs):raise urllib.error.HTTPError('https://api.groq.com',code,'error',{},None)
    monkeypatch.setattr('scripts.check_groq_key.urllib.request.urlopen',transport)
    result=check_key()
    assert result['key_valid'] is valid and result['status']==status
    assert result['requests']==1


def test_windows_network_block_is_not_an_invalid_key(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY','test-only-secret')
    attempts=[]
    def transport(*args,**kwargs):
        attempts.append(1)
        raise urllib.error.URLError(PermissionError(10013,'Network denied'))
    monkeypatch.setattr('scripts.check_groq_key.urllib.request.urlopen',transport)
    result=check_key()
    assert result['status']=='unverified' and result['key_valid'] is None
    assert result['reason']=='local_network_access_denied' and result['error_code']==10013
    assert len(attempts)==1


def test_missing_key_makes_no_request(monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    monkeypatch.setattr('scripts.check_groq_key.urllib.request.urlopen',lambda *a,**k:pytest.fail('No key must not send a request'))
    assert check_key()['requests']==0
