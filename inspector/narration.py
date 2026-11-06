"""Server-side Groq narration with a closed set of finding-bound statements and fixes."""
from functools import lru_cache
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from inspector.auto_inspect import FIXES

ENV_FILE=Path(__file__).resolve().parents[1]/'.env'


def read_groq_env(path):
    """Read only supported settings without retaining stale file values in os.environ."""
    path=Path(path)
    if not path.is_file():return {}
    settings={}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        name,separator,value=line.partition('=')
        name=name.strip()
        if separator and name in {'GROQ_API_KEY','GROQ_MODEL'}:
            value=value.strip()
            if len(value)>=2 and value[0]==value[-1] and value[0] in {'"',"'"}:
                value=value[1:-1]
            settings[name]=value
    return settings


def load_groq_env(path):
    """Load CLI settings while preserving nonempty process environment values."""
    for name,value in read_groq_env(path).items():
        if not os.environ.get(name):os.environ[name]=value


def approved_wording(report):
    items=[]
    for finding in report['checks']:
        key=finding['identifier'].rsplit('.',1)[-1]
        fix='' if finding['status'] in {'Pass','Disabled'} else FIXES.get(key,'Review the evidence for this check before using the photo.')
        if finding['status']=='Manual Review':
            if finding.get('reason_code')=='background_dominated_contrast':
                fix='Review product detail and the group-size finding. A mostly blank background does not establish poor product contrast.'
            elif finding.get('reason_code') in {'intended_group_unconfirmed','intended_group_missing'}:
                fix=FIXES['coverage']
            elif finding.get('reason_code') in {'calibration_unavailable','composition_policy_unavailable'}:
                fix='Review the original at full size; this measurement needs a supported threshold before an automatic decision.'
            elif key in {'blur','noise','contrast','shadow_loss','highlight_loss'}:
                fix='This check is uncertain. Review the original at full size. If the issue is visible: '+fix
        items.append({'finding_id':finding['identifier'],'statement':finding['explanation'],'fix':fix})
    return items


def validate(payload, approved):
    if not isinstance(payload,dict) or set(payload)!={'items'} or not isinstance(payload['items'],list):
        raise ValueError('invalid_schema')
    allowed={item['finding_id']:item for item in approved}
    seen=set()
    for item in payload['items']:
        if not isinstance(item,dict) or set(item)!={'finding_id','statement','fix'}:
            raise ValueError('invalid_schema')
        identity=item['finding_id']
        if not isinstance(identity,str) or identity not in allowed or identity in seen:
            raise ValueError('unknown_or_duplicate_finding')
        # IDs alone do not prove a claim: novel text or a cross-finding statement is rejected.
        if item['statement']!=allowed[identity]['statement'] or item['fix']!=allowed[identity]['fix']:
            raise ValueError('unsupported_claim')
        seen.add(identity)
    if seen!=set(allowed):raise ValueError('missing_findings')
    return payload['items']


@lru_cache(maxsize=128)
def _explain(finding_json, model, key):
    approved=json.loads(finding_json)
    fallback={'source':'local','items':approved,'reason':'not_configured'}
    if not key:return fallback
    if len(approved)>48:return {**fallback,'reason':'finding_limit'}
    schema={'type':'object','properties':{'items':{'type':'array','items':{
        'type':'object','properties':{'finding_id':{'type':'string','enum':[i['finding_id'] for i in approved]},
        'statement':{'type':'string'},'fix':{'type':'string'}},
        'required':['finding_id','statement','fix'],'additionalProperties':False}}},
        'required':['items'],'additionalProperties':False}
    body={'model':model,'temperature':0,'max_completion_tokens':min(4096,max(512,len(finding_json)//2+256)),
        'messages':[{'role':'system','content':'Arrange the backend-approved plain-language findings and fixes for a product-photo report. Return every item exactly once. Copy each statement/fix verbatim for its finding_id. Do not add claims, causes, category names, verdicts, measurements, or promises. The backend already owns the verdict.'},
                    {'role':'user','content':json.dumps({'approved_items':approved})}],
        'response_format':{'type':'json_schema','json_schema':{'name':'grounded_findings','strict':True,'schema':schema}}}
    if model in {'openai/gpt-oss-20b','openai/gpt-oss-120b'}:body['reasoning_effort']='low'
    request=urllib.request.Request('https://api.groq.com/openai/v1/chat/completions',
        data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+key,
        'Content-Type':'application/json','User-Agent':'Framecheck/1.0'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=4) as response:
            raw=response.read(512_001)
        if len(raw)>512_000:raise ValueError('response_too_large')
        envelope=json.loads(raw)
        message=envelope['choices'][0]
        if message.get('finish_reason')!='stop':raise ValueError('incomplete_response')
        items=validate(json.loads(message['message']['content']),approved)
        usage=envelope.get('usage',{})
        usage={name:usage[name] for name in ['prompt_tokens','completion_tokens','total_tokens']
               if isinstance(usage,dict) and type(usage.get(name)) is int and usage[name]>=0}
        return {'source':'groq_validated','items':items,'reason':None,'usage':usage}
    except urllib.error.HTTPError as error:
        return {**fallback,'reason':'api_http_'+str(error.code)}
    except OSError as error:
        cause=getattr(error,'reason',error)
        denied=isinstance(cause,PermissionError) or getattr(cause,'errno',None) in {13,10013} or getattr(cause,'winerror',None)==10013
        reason='api_network_access_denied' if denied else 'api_timeout' if isinstance(cause,TimeoutError) else 'api_unavailable'
        return {**fallback,'reason':reason}
    except (ValueError,KeyError,IndexError,TypeError):
        return {**fallback,'reason':'api_failed_or_unsupported_claim'}


def explain(report):
    # Only backend text and finding IDs leave the server; no image, filename, path, key or user prose.
    approved=approved_wording(report)
    try:
        settings=read_groq_env(ENV_FILE)
    except (OSError,UnicodeError):
        return {'source':'local','items':approved,'reason':'configuration_unreadable'}
    key=(os.environ.get('GROQ_API_KEY') or settings.get('GROQ_API_KEY','')).strip()
    model=(os.environ.get('GROQ_MODEL') or settings.get('GROQ_MODEL') or 'openai/gpt-oss-20b').strip()
    return _explain(json.dumps(approved,sort_keys=True),model,key)
