"""Check Groq authentication with one model-list request and no text generation."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from inspector.narration import load_groq_env

ROOT=Path(__file__).resolve().parents[1]


def check_key():
    result={'key_valid':None,'requests':0,'generation_requests':0,'generated_tokens':0}
    key=os.environ.get('GROQ_API_KEY','').strip()
    if not key:return {**result,'status':'unverified','reason':'key_not_configured'}
    request=urllib.request.Request('https://api.groq.com/openai/v1/models',
        headers={'Authorization':'Bearer '+key,'User-Agent':'Framecheck/key-check'},method='GET')
    result['requests']=1
    try:
        with urllib.request.urlopen(request,timeout=8) as response:
            payload=json.loads(response.read(512_001))
            if response.status!=200 or not isinstance(payload,dict) or not isinstance(payload.get('data'),list):
                raise ValueError('Unexpected model-list response')
        return {**result,'status':'verified','key_valid':True,'http_status':200}
    except urllib.error.HTTPError as error:
        return {**result,'status':'rejected' if error.code==401 else 'unverified',
            'key_valid':False if error.code==401 else None,'http_status':error.code,
            'reason':'authentication_rejected' if error.code==401 else 'provider_http_error'}
    except OSError as error:
        cause=getattr(error,'reason',error)
        code=getattr(cause,'winerror',None) or getattr(cause,'errno',None)
        denied=isinstance(cause,PermissionError) or code in {13,10013}
        return {**result,'status':'unverified','reason':'local_network_access_denied' if denied else 'network_unavailable',
            'error_code':code}
    except (ValueError,TypeError):
        return {**result,'status':'unverified','reason':'unexpected_provider_response'}


def main():
    load_groq_env(ROOT/'.env')
    result={'checked_at':datetime.now(timezone.utc).isoformat(),**check_key()}
    destination=ROOT/'reports/groq-key-check/latest.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='verified' else 1 if result['status']=='rejected' else 2


if __name__=='__main__':raise SystemExit(main())
