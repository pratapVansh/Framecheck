"""Server-side Groq narration with a closed set of finding-bound statements and fixes."""
from functools import lru_cache
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

from inspector.auto_inspect import FIXES
from inspector.scoring import score_statement
from inspector.story import story_options, deterministic_story, validate_story

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
        if key=='group_size' and finding['status']=='Fail':
            value=finding.get('measurement',{}).get('score')
            minimum=finding.get('threshold',{}).get('minimum')
            maximum=finding.get('threshold',{}).get('maximum')
            if value is not None and minimum is not None and value<minimum:
                fix='Move closer so the products fill more of the photo. Keep their edges visible.'
            elif value is not None and maximum is not None and value>maximum:
                fix='Step back so every product fits in the photo with space around its edges.'
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
def _explain(finding_json, model, key, score_json, story_json):
    approved=json.loads(finding_json)
    score=json.loads(score_json)
    score_text=score_statement(score) if score else None
    options=json.loads(story_json)
    fallback={'source':'local','items':approved,'reason':'not_configured',
              'story':deterministic_story(options)}
    if score_text:fallback['score_explanation']=score_text
    if not key:return fallback
    if len(approved)>48:return {**fallback,'reason':'finding_limit'}
    def claims_schema(choices):
        ids=[item['id'] for item in choices]
        texts=[text for item in choices for text in item['texts']]
        return {'type':'array','minItems':len(choices),'maxItems':len(choices),'items':{
            'type':'object','properties':{
                'id':{'type':'string',**({'enum':ids} if ids else {})},
                'finding_ids':{'type':'array','items':{'type':'string'}},
                'text':{'type':'string',**({'enum':texts} if texts else {})}},
            'required':['id','finding_ids','text'],'additionalProperties':False}}
    schema={'type':'object','properties':{'story':{'type':'object','properties':{
        'headline':{'type':'string','enum':list(dict.fromkeys(options['headline']))},
        'summary':{'type':'string','enum':list(dict.fromkeys(options['summary']))},
        'good':claims_schema(options['good']),'actions':claims_schema(options['actions'])},
        'required':['headline','summary','good','actions'],'additionalProperties':False}},
        'required':['story'],'additionalProperties':False}
    referenced={identity for group in options['good']+options['actions'] for identity in group['finding_ids']}
    supplied=[item for item in approved if item['finding_id'] in referenced]
    body={'model':model,'temperature':0,'max_completion_tokens':2048,
        'messages':[{'role':'system','content':
            'Compose a short, friendly photo review for someone who does not know image-processing terms. '
            'Return only {"story": {"headline": "...", "summary": "...", "good": [...], "actions": [...]}}. '
            'The backend supplies supported alternatives in story_evidence. Choose the most natural headline, '
            'summary, and one text alternative for each supplied good/action claim. Keep its exact id and '
            'finding_ids. Keep all actions in their supplied priority order. You may reorder strengths. '
            'Use only supplied alternatives: do not add words, causes, promises, questions, or new claims. '
            'An incomplete check is not a confirmed defect. Never claim the photo looks good unless that '
            'headline is supplied. Do not alter scores, verdicts, product scopes, or certainty. '
            'Do not return measurements, checklists, extra advice, or any additional keys.'},
            {'role':'user','content':json.dumps({'story_evidence':options,'approved_items':supplied,
                **({'score_evidence':score,'approved_score_explanation':score_text} if score_text else {})})}],
        'response_format':{'type':'json_schema','json_schema':{'name':'friendly_photo_review','strict':True,'schema':schema}}}
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
        payload=json.loads(message['message']['content'])
        if not isinstance(payload,dict) or set(payload)!={'story'}:
            raise ValueError('invalid_story_schema')
        story=validate_story(payload['story'],options)
        usage=envelope.get('usage',{})
        usage={name:usage[name] for name in ['prompt_tokens','completion_tokens','total_tokens']
               if isinstance(usage,dict) and type(usage.get(name)) is int and usage[name]>=0}
        return {**fallback,'source':'groq_validated','story':story,'reason':None,'usage':usage}
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
    options=story_options(report)
    try:
        settings=read_groq_env(ENV_FILE)
    except (OSError,UnicodeError):
        return {'source':'local','items':approved,'reason':'configuration_unreadable','story':deterministic_story(options),
                **({'score_explanation':score_statement(report['quality_score'])} if report.get('quality_score') else {})}
    key=(os.environ.get('GROQ_API_KEY') or settings.get('GROQ_API_KEY','')).strip()
    model=(os.environ.get('GROQ_MODEL') or settings.get('GROQ_MODEL') or 'openai/gpt-oss-20b').strip()
    return _explain(json.dumps(approved,sort_keys=True),model,key,json.dumps(report.get('quality_score'),sort_keys=True),
                    json.dumps(options,sort_keys=True))
