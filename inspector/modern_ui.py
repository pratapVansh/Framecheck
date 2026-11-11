"""Responsive one-click upload/results, with evidence and advanced controls kept in Details."""
import html
import json
from uuid import uuid4

from inspector.auto_inspect import CheckResult, PIPELINE_VERSION, aggregate_required as aggregate, detected_regions
from inspector.narration import approved_wording
from inspector.story import story_options, deterministic_story, friendly_finding
from inspector.product_forms import marking_form
from inspector.scoring import LABELS, WEIGHTS, score_statement

PRODUCT_LABELS={'detail':'Product detail','blur':'Focus','noise':'Graininess',
                'contrast':'Visible detail','clipping':'Bright and dark detail',
                'mask_completeness':'Product outline','edge':'Photo edges'}

def esc(value):return html.escape(str(value),quote=True)


CSS='''
:root{font:16px/1.55 Inter,ui-sans-serif,system-ui,sans-serif;color:#172524;background:#f4f6f3;--green:#164b3f;--muted:#65716b;--line:#dfe5de}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0}a{color:var(--green)}header{max-width:1240px;margin:auto;padding:25px 28px;display:flex;justify-content:space-between;align-items:center}header a{text-decoration:none;font-weight:650}.brand{font-size:23px;letter-spacing:-1px;white-space:nowrap}.brand i{display:inline-block;border:3px solid #50796b;width:19px;height:19px;vertical-align:-1px;margin-right:9px;border-radius:5px}nav{display:flex;gap:24px;font-size:14px}main{max-width:1184px;margin:18px auto 65px;padding:0 28px}.hero{text-align:center;padding:38px 10px 24px}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.16em;font-weight:700;color:#557366}h1{font-size:clamp(34px,5vw,60px);line-height:1.1;letter-spacing:-2.6px;margin:15px 0}h2{font-size:22px;letter-spacing:-.6px;margin:0 0 16px}h3{font-size:17px;margin:0}p{margin:8px 0;color:var(--muted)}.lead{max-width:640px;margin:18px auto}.upload-panel{max-width:760px;margin:20px auto}.dropzone{display:block;text-align:center;background:#fff;border:1.5px dashed #9bb3a6;border-radius:24px;padding:54px 24px;cursor:pointer;box-shadow:0 18px 48px #233b2c06}.dropzone.drag{border-color:#164b3f;background:#e6f0e8}.upload-icon{display:grid;place-items:center;width:58px;height:58px;margin:0 auto 18px;background:#eef4ed;border-radius:17px;color:#164b3f;font-size:28px}.button,button{display:inline-block;border:0;border-radius:12px;background:#164b3f;color:#fff;font:inherit;font-weight:650;padding:12px 21px;cursor:pointer;text-decoration:none}.subtle{font-size:13px;color:var(--muted)}.hidden-input{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.card{background:white;border:1px solid var(--line);border-radius:19px;padding:23px;margin-bottom:18px}.upload-meta{display:flex;justify-content:center;gap:24px;flex-wrap:wrap;margin:17px 0;font-size:13px;color:var(--muted)}.pill{display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border-radius:30px;font-size:12px;font-weight:750;white-space:nowrap}.pass{background:#e2f2e8;color:#225f40}.fail{background:#fde8e5;color:#9c342b}.manual-review{background:#fff0d4;color:#8b5b1b}.disabled{background:#eef0ed;color:#6b756e}.result-head{display:flex;justify-content:space-between;gap:20px;align-items:center;padding:16px 0 20px}.result-head h1{font-size:40px;letter-spacing:-1.6px;margin:8px 0}.verdict{font-size:17px;padding:9px 18px}.summary{max-width:740px}.coverage{border-left:3px solid #b3c6b7;padding:11px 15px;background:#edf3ec;border-radius:0 12px 12px 0;margin:10px 0 22px;font-size:14px;color:#354d40}.coverage.incomplete{background:#fff5e4;border-color:#dca958;color:#755426}.result-grid{display:grid;grid-template-columns:minmax(0,1.03fr) minmax(0,1fr);gap:26px;align-items:start}.photo-panel{position:sticky;top:18px}.photo-panel svg{display:block;width:100%;max-height:650px;background:#f4f5f1;border-radius:14px}.photo-panel .subtle{margin-top:10px}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px}.product-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:11px}.number{background:#164b3f;color:white;display:inline-grid;place-items:center;min-width:29px;height:29px;border-radius:9px;font-weight:750}.finding{display:grid;grid-template-columns:9px 1fr;gap:10px;padding:9px 0;border-bottom:1px solid #f0f2ed}.finding:last-child{border:0}.dot{width:7px;height:7px;border-radius:50%;margin-top:9px;background:#5a9570}.dot.fail{background:#cd6957}.dot.manual-review{background:#c28c36}.finding p{margin:0;font-size:14px;color:#35443c}.ref{font-size:11px;color:#728477;margin-left:5px;text-decoration:none}.fix{padding:11px 0;border-bottom:1px solid #edf0e9;font-size:14px}.fix:last-child{border:0}.fix b{color:#315543;font-size:12px;display:block}.notice{background:#fff4df;border:1px solid #f0d6a2;padding:14px;border-radius:12px;margin:14px 0}.error{background:#fff0ed;border-color:#e7b9ad;color:#9a3427}.details{margin-top:26px}summary{cursor:pointer;font-weight:650;color:#42604d;padding:10px 0;font-size:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,monospace;background:#f5f6f2;padding:15px;border-radius:10px}input,select{font:inherit;padding:10px;border:1px solid #c9d3c8;border-radius:9px;max-width:100%}button:focus-visible,a:focus-visible,label:focus-within,svg:focus-visible,summary:focus-visible{outline:3px solid #bd994a;outline-offset:4px}footer{max-width:1184px;margin:30px auto;padding:20px 28px;color:var(--muted);font-size:12px}.empty{padding:28px;text-align:center}.evidence{display:grid;grid-template-columns:1fr 1fr;gap:15px}.evidence img{width:100%;max-height:350px;object-fit:contain}.history-row{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 0;border-bottom:1px solid var(--line)}#product-marking svg{width:100%;max-height:380px}#product-marking button{font-size:12px;padding:9px 10px;margin:4px 0}#product-marking p{font-size:13px}#product-marking h2{font-size:18px}.source{font-size:12px;color:var(--muted)}@media(max-width:850px){.result-grid{grid-template-columns:1fr}.photo-panel{position:static}.photo-panel svg{max-height:500px}.result-head{align-items:flex-start}.hero{padding-top:24px}}@media(max-width:480px){header{padding:20px}.brand{font-size:21px}nav{gap:2px;flex-direction:column;align-items:flex-end;font-size:12px}main{padding:0 18px}.card{padding:18px}.dropzone{padding:35px 15px}h1{letter-spacing:-1.4px}.result-head h1{font-size:30px}.result-head{flex-wrap:wrap}.upload-meta{gap:10px}.evidence{grid-template-columns:1fr}}
'''


def page(title,body):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Framecheck</title><style>{CSS}</style></head><body><header><a class="brand" href="/"><i aria-hidden="true"></i>Framecheck</a><nav><a href="/">Inspect a photo</a><a href="/history">Saved inspections</a></nav></header><main>{body}</main><footer>Inspection stays local. Optional explanations use structured findings, never the photo. Pilot results are not marketplace guarantees.</footer></body></html>'''


def badge(status,extra=''):
    label='Needs a quick check' if status=='Manual Review' and extra=='verdict' else status
    if extra=='product':label={'Pass':'Looks good','Fail':'Needs an update','Manual Review':'Needs a closer look','Disabled':'Not checked'}.get(status,status)
    color={'Needs Improvement':'fail','Needs Review':'manual-review'}.get(status,status.lower().replace(' ','-'))
    return f'<span class="pill {esc(color)} {extra}">{esc(label)}</span>'


def home(error=None):
    notice=f'<p role="alert" class="notice error">{esc(error)}</p>' if error else ''
    return page('Product photo inspector',f'''<section class="hero"><span class="eyebrow">Product photo inspector</span><h1>One photo.<br>Clear findings.</h1><p class="lead">Check the whole photo and every product region we can identify. Get a verdict, understand the issues, and see what to fix.</p></section><section class="upload-panel">{notice}<form method="post" action="/inspect" enctype="multipart/form-data" id="one-click-upload"><input type="hidden" name="submission_id" value="{uuid4()}"><input type="hidden" name="one_click" value="yes"><label class="dropzone" id="dropzone" for="image"><span class="upload-icon" aria-hidden="true">＋</span><span class="button">Upload a product photo</span><p>or drop it here</p><span class="subtle">JPG or PNG · up to 10 MB · original photos work best</span><input class="hidden-input" id="image" name="image" type="file" accept="image/jpeg,image/png" required></label><button type="submit" id="upload-fallback">Inspect photo</button><p id="upload-status" role="status" class="subtle"></p></form><div class="upload-meta"><span>Photo checks</span><span>Automatic product regions</span><span>Practical next steps</span></div><details class="card"><summary>Details and advanced settings</summary><p class="subtle">DIP determines the verdict. Detection and calibration limits stay visible in the result. If coverage is unclear, available findings appear before any question.</p><a href="/?advanced=1">Open manual category and region tools</a></details></section><script>
const uploadForm=document.getElementById('one-click-upload'), fileInput=document.getElementById('image'), drop=document.getElementById('dropzone');
document.getElementById('upload-fallback').hidden=true;
fileInput.addEventListener('change',()=>{{if(fileInput.files.length)uploadForm.requestSubmit();}});
uploadForm.addEventListener('submit',()=>{{document.getElementById('upload-status').textContent='Inspecting your photo and product regions…';drop.style.pointerEvents='none';}});
drop.addEventListener('dragover',event=>{{event.preventDefault();drop.classList.add('drag');}});
drop.addEventListener('dragleave',()=>drop.classList.remove('drag'));
drop.addEventListener('drop',event=>{{event.preventDefault();drop.classList.remove('drag');if(event.dataTransfer.files.length){{fileInput.files=event.dataTransfer.files;uploadForm.requestSubmit();}}}});
</script>''')


CSS += '''
.review{--ink:#1b3029;--quiet:#748078;--accent:#1c5947;color:var(--ink)}.review-top{display:flex;justify-content:space-between;gap:24px;align-items:end;margin:28px 0}.review-top h1{font-size:36px;letter-spacing:-1.5px;margin:8px 0}.review-top p{font-size:13px}.review-links{display:flex;gap:16px;align-items:center;font-size:13px}.review-overview{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);background:white;border:1px solid var(--line);border-radius:24px;overflow:hidden;box-shadow:0 14px 50px #233b2c07;margin-bottom:32px}.review-photo{min-width:0;margin:0;background:#eaece6;padding:20px;display:flex;flex-direction:column;justify-content:center}.review-photo svg{width:100%;height:auto;max-height:520px;display:block}.review-photo figcaption{display:flex;justify-content:space-between;gap:16px;font-size:12px;margin-top:15px;color:#627068}.score-panel{padding:36px;display:flex;flex-direction:column;justify-content:center;align-items:start;gap:20px}.score-label{font-size:12px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:#62786a}.score-value{font-size:96px;line-height:1;font-weight:650;letter-spacing:-6px;font-variant-numeric:tabular-nums}.score-value small{font-size:26px;color:#8b968e;letter-spacing:-1px;font-weight:400;margin-left:8px}.score-panel .verdict{font-size:13px;padding:6px 12px}.score-panel p{font-size:15px;line-height:1.65;margin:0;color:#53655b}.score-coverage{width:100%;padding-top:20px;border-top:1px solid var(--line);font-size:12px;color:#627068}.score-coverage>div{display:flex;justify-content:space-between;gap:12px}.coverage-track{height:5px;border-radius:4px;background:#e9eee7;overflow:hidden;margin:10px 0}.coverage-track span{display:block;height:100%;background:#447b5b}.review-content{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:28px;align-items:start}.review-section-title{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:15px;gap:16px}.review-section-title h2{font-size:21px;margin:0}.review-section-title span{font-size:12px;color:#748078}.product-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.product-card{background:white;border:1px solid var(--line);border-radius:17px;padding:20px;min-width:0}.product-card .product-head{gap:9px;margin-bottom:17px}.product-card h3{font-size:16px}.product-card .number{height:27px;min-width:27px;font-size:12px;border-radius:8px}.product-card>.pill{margin-bottom:13px}.product-signal{display:flex;justify-content:space-between;gap:8px;padding:8px 0;border-top:1px solid #eff2ec;font-size:12px;color:#647268}.signal-value{color:#305d42;font-weight:600}.signal-value.review-state{color:#986d2b}.signal-value.fail-state{color:#a84b3c}.product-card>a{font-size:12px;display:inline-block;margin-top:13px}.action-card{padding:24px;background:#eaf0e8;border:1px solid #d8e3d6;border-radius:17px}.action-card h2{font-size:21px}.action-item{padding:17px 0;border-bottom:1px solid #d5e0d2}.action-item:last-child{border:0;padding-bottom:0}.action-item h3{font-size:15px;line-height:1.5}.action-item p{font-size:13px;line-height:1.65;color:#5c7162;margin:8px 0}.action-item a{font-size:12px;font-weight:600}.action-scope{display:block;color:#677f6c;font-size:10px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:5px}.review-details{margin-top:30px;padding:4px 24px;background:white;border:1px solid var(--line);border-radius:17px}.review-details>summary{font-size:16px;padding:20px 0;color:#263f31}.review-details>summary span{font-weight:400;color:#7d8981;font-size:12px;margin-left:14px}.review-details[open]{padding-bottom:24px}.detail-section{padding:24px 0;border-top:1px solid var(--line)}.detail-section h3{margin-bottom:12px}.detail-section p{font-size:13px}.context-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.context-item{display:flex;align-items:start;gap:15px;background:#f5f6f2;padding:15px;border-radius:12px}.region-preview{width:75px;height:85px;flex-shrink:0;background:#e9ece6;border-radius:8px}.context-item h4{margin:0 0 5px;font-size:13px}.context-item p{font-size:12px;margin:0}.evidence-table-wrap{overflow-x:auto}.evidence-table{width:100%;border-collapse:collapse;font-size:12px}.evidence-table th,.evidence-table td{text-align:left;padding:10px 9px;border-bottom:1px solid #edf0e9;vertical-align:top}.evidence-table th{color:#6b7d70;font-weight:600;white-space:nowrap}.evidence-table td:first-child{min-width:155px}.intent-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:15px 0}.intent-choice{display:flex;align-items:center;gap:10px;padding:12px;background:#f4f6f0;border-radius:10px;font-size:13px}.intent-choice input{accent-color:#215a43;width:17px;height:17px;flex-shrink:0}.intent-choice .region-preview{width:45px;height:50px}.review-details fieldset{border:0;padding:0;margin:15px 0}.review-details legend{font-weight:600;font-size:14px}.review-details .evidence{margin:18px 0}.review-details figure{margin:0}.review-details .finding p{font-size:13px}.historical-note{display:flex;gap:15px;align-items:center;justify-content:space-between;background:#faf1de;border:1px solid #eddfbc;border-radius:12px;padding:14px 18px;margin-bottom:20px;font-size:13px}.historical-note p{margin:0}.historical-note button{font-size:12px;white-space:nowrap;padding:9px 13px}.review-footnote{font-size:11px;color:#839085;margin:18px 0}.evidence-link{font-size:12px}.action-item [data-fix]{display:block}.product-card:target{outline:2px solid #669576;outline-offset:4px}
@media(max-width:850px){.review-overview,.review-content{grid-template-columns:1fr}.review-photo svg{max-height:550px}.score-panel{padding:28px;gap:15px}.score-value{font-size:78px}.review-content{gap:25px}.review-top{margin-top:15px}.review-top h1{font-size:31px}.review-links{flex-direction:column;gap:8px;align-items:end}.score-panel .score-coverage{max-width:none}.review-photo{padding:16px}}@media(max-width:480px){.review-top{align-items:start;gap:12px}.review-top h1{font-size:27px;letter-spacing:-1px}.review-links{font-size:11px}.review-overview{border-radius:17px;margin-bottom:25px}.score-panel{padding:23px}.score-value{font-size:75px}.product-cards,.context-list,.intent-options{grid-template-columns:1fr}.product-card{padding:18px}.review-section-title span{font-size:11px}.review-details{padding:2px 18px}.review-details>summary span{display:block;margin:3px 0 0 18px}.action-card{padding:21px}.historical-note{flex-direction:column;align-items:start}.score-coverage>div{flex-wrap:wrap}}
'''


def numbered_photo(result,identity):
    w,h=result['width'],result['height'];font=max(22,round(w/35));marks=[]
    products=[p for p in detected_regions(result['segmentation']) if p.get('intended',True)]
    for number,product in enumerate(products,1):
        x,y,_,_=product['bbox']
        marks.append(f'<a href="#product-{number}" aria-label="View product {number} findings" data-product-number="{number}"><circle cx="{x+font*.7}" cy="{y+font*.7}" r="{font*.68}" fill="#164b3f" stroke="white" stroke-width="2"/><text x="{x+font*.7}" y="{y+font*1.02}" text-anchor="middle" font-size="{font}" font-weight="650" fill="white">{number}</text></a>')
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Original photo with intended products numbered"><image href="/evidence/{identity}/original.png" width="{w}" height="{h}"/>{"".join(marks)}</svg>'


CSS += '''
.review-overview{grid-template-areas:"photo score"}.review-photo{grid-area:photo}.score-panel{grid-area:score;min-width:0}.primary-suggestion{width:100%;padding:16px;background:#edf3eb;border:1px solid #d8e3d6;border-radius:12px;box-sizing:border-box}.primary-suggestion h3{font-size:16px}.primary-suggestion p{font-size:13px;margin:8px 0 12px}.primary-suggestion .button{font-size:13px;max-width:100%;box-sizing:border-box}.primary-suggestion a{font-size:13px}.score-panel{gap:14px}
@media(max-width:850px){.review-overview{grid-template-areas:"score" "photo"}}
@media(max-width:480px){.score-panel{padding:22px;gap:12px}.score-value{font-size:76px}.primary-suggestion{padding:14px}}
'''

def region_preview(product,result,identity):
    x,y,w,h=product['bbox']
    return f'<svg class="region-preview" viewBox="{x} {y} {w} {h}" role="img" aria-label="{esc(product.get("label","Detected region"))}"><image href="/evidence/{identity}/original.png" width="{result["width"]}" height="{result["height"]}"/></svg>'


def render_result(record, error=None):
    result=json.loads(record['result_json']);identity=esc(result['inspection_id']);meta=result['segmentation']
    regions=detected_regions(meta)
    products=[p for p in regions if p.get('intended',True)]
    incidental=[p for p in regions if not p.get('intended',True)]
    numbers={p.get('region_id',p['number']):n for n,p in enumerate(products,1)}
    wording={item['finding_id']:item for item in approved_wording(result)}
    checks={c['identifier']:c for c in result['checks']}
    score=result.get('quality_score')
    options=story_options(result);story=deterministic_story(options)
    story_actions={item['id']:item for item in story['actions']}
    def scope(finding_id):
        if finding_id.startswith('photo.'):return 'Photo'
        region=int(finding_id.split('.')[0].split('-')[-1])
        return f'Product {numbers[region]}' if region in numbers else 'Incidental context'
    def evidence(check):
        finding_id=check['identifier'];state=check['status'].lower().replace(' ','-')
        advisory='' if check.get('required',True) else '<p class="subtle">Context only; excluded from verdict and score.</p>'
        title=LABELS.get(finding_id.rsplit('.',1)[-1],finding_id)
        return f'<details id="evidence-{esc(finding_id)}"><summary>{esc(scope(finding_id))} · {esc(title)} · {esc(check["status"])}</summary>{advisory}<div class="finding"><span class="dot {state}"></span><p data-finding-id="{esc(finding_id)}" data-statement>{esc(wording[finding_id]["statement"])}</p></div><pre>{esc(json.dumps(check,indent=2))}</pre></details>'
    cards=[]
    for number,product in enumerate(products,1):
        local=[checks[i] for i in product['finding_ids'] if i in checks]
        state=str(aggregate([CheckResult(**c) for c in local]))
        region=product.get('region_id',product['number'])
        expected=set(product['finding_ids'])
        if score:expected.update(f'product-{region}.{key.split(".")[1]}' for key in WEIGHTS if key.startswith('product.'))
        missing=sorted(expected-set(checks))
        blockers=[c for c in local if c.get('required',True) and c['status']!='Pass']
        if (missing or any(c['status']!='Fail' for c in blockers)) and state!='Fail':state='Manual Review'
        blocking_html=''
        for check in blockers:
            fid=check['identifier'];kind='Confirmed problem' if check['status']=='Fail' else 'Needs a closer look'
            title=PRODUCT_LABELS.get(fid.rsplit('.',1)[-1],'Product detail')
            blocking_html+=(f'<div class="product-blocker" data-blocking-finding="{esc(fid)}"><strong>{kind}: {esc(title)}</strong>'
                            f'<p data-finding-id="{esc(fid)}" data-blocker-statement>{esc(friendly_finding(check))}</p>'
                            f'<a href="#evidence-{esc(fid)}">View this finding</a></div>')
        for fid in missing:
            blocking_html+=f'<div class="product-blocker" data-blocking-finding="{esc(fid)}"><strong>Needs a closer look: {esc(PRODUCT_LABELS.get(fid.rsplit(".",1)[-1],"Product detail"))}</strong><p>This part of the review did not finish. Review the photo again.</p></div>'
        if not blockers and not missing:
            blocking_html='<p class="product-clear">No concerns from the checks for this item.</p>'
        first=blockers[0]['identifier'] if blockers else product['finding_ids'][0]
        label=esc(product.get('label','Product region'))
        cards.append(f'<article class="product-card" id="product-{number}" data-intended-product="{number}"><div class="product-head"><span class="number">{number}</span><h3>{label}</h3></div>{badge(state,'product')}{blocking_html}<a href="#evidence-{esc(first)}">See the review details</a></article>')
    # Group repeated advice by defect; uncertainty stays conditional and evidence-bound.
    fixes=[];primary_html=''
    for action in options['actions']:
        item=story_actions[action['id']];fid=action['finding_ids'][0] if action['finding_ids'] else None
        kind=action['kind'];target=action['target']
        link=('/evidence/'+identity+'/original.png' if target=='original' else
              '#coverage-question' if target=='selection' else '#evidence-'+esc(fid) if fid in checks else '#score-evidence')
        attributes='target="_blank" rel="noopener"' if target=='original' else ''
        assessment='To improve' if kind=='confirmed' else 'Needs a closer look'
        fixes.append(f'<div class="action-item" data-action-kind="{kind}"><span class="action-scope">{assessment}: {esc(action["scope"])}</span><h3>{esc(action["title"])}</h3><p data-story-action="{esc(action["id"])}">{esc(item["text"])}</p><a href="{link}" {attributes}>See this area</a></div>')
        if not primary_html:
            control=(f'<form method="post" action="/reinspect" enctype="multipart/form-data"><input type="hidden" name="source_inspection_id" value="{identity}"><input type="hidden" name="submission_id" value="{uuid4()}"><button>Review this photo again</button></form>' if target=='reinspect' else
                     f'<a class="button" href="{link}" {attributes} data-finding-id="{esc(fid or "")}">{esc(action["title"])}</a>')
            primary_html=f'<section class="primary-suggestion" data-action-kind="{kind}" aria-label="Next step">{control}</section>'
    fix_html=''.join(fixes)
    next_steps=f'<section class="action-card"><h2>Make it even better</h2>{fix_html}</section>' if fixes else ''
    if fixes and result['status']!='Pass':next_steps=next_steps.replace('Make it even better','What to do next')
    content_class='review-content' if next_steps else 'review-content no-actions'
    good_html=''.join(f'<span data-story-good="{esc(item["id"])}">{esc(item["text"])}</span> ' for item in story['good'])
    strengths=f'<div class="story-strengths"><span class="eyebrow">What works</span><p>{good_html}</p></div>' if good_html else ''
    count=len(products)
    coverage=meta['coverage']
    correction=(f'<p class="subtle">{count} product{"" if count==1 else "s"} selected. '
                '<a href="#coverage-question">Change products (optional)</a></p>' if regions else '')
    dependents=[c for c in result['checks'] if c.get('measurement',{}).get('blocked_by')=='photo.coverage']
    selection_ids={'photo.coverage',*(c['identifier'] for c in dependents)} if dependents else set()
    findings_html=''.join(evidence(c) for c in result['checks'] if c['identifier'] not in selection_ids)
    if dependents:
        pending=sum(c['withheld'] for c in score['components'] if c['component'] in selection_ids) if score else 0
        findings_html+=(f'<section id="selection-blocker"><h3>Product selection and framing</h3>'
                        f'<p>Framing waits for the same intended-product selection. {pending} points remain pending for this shared selection blocker. '
                        'Each check keeps its own score weight. Focus findings remain independent.</p>'+
                        ''.join(evidence(checks[identity]) for identity in ['photo.coverage',*(c['identifier'] for c in dependents)])+'</section>')
    score_value=f'{score["score"]}<small>/ 100</small>' if score else '<small>Not scored</small>'
    assessment_summary=(f'<div class="assessment-summary"><p><strong>Confirmed problems:</strong> {score["confirmed_deductions"]} points deducted.</p>'
                        f'<p><strong>Incomplete assessment:</strong> {score["uncertainty_withheld"]} points pending.</p></div>' if score else '')
    explanation=score_statement(score) if score else 'This saved inspection predates whole-photo scoring. Reinspect the original to use the current rules.'
    required=[c for c in result['checks'] if c.get('required',True)]
    completed=sum(c['status'] in {'Pass','Fail'} for c in required)
    completion_percent=100*completed/len(required) if required else 0
    coverage_html=(f'<div class="score-coverage"><div><span>Checks completed</span><strong>{completed} of {len(required)}</strong></div><div class="coverage-track" aria-hidden="true"><span style="width:{completion_percent}%"></span></div><span>Completed checks can pass or find a problem. <a href="#score-evidence">Score details</a></span></div>' if score else '')
    old=''
    provenance=meta.get('provenance',{})
    operation=provenance.get('operation')
    corrected=operation=='correction' or (not provenance and meta.get('source_inspection_id'))
    if corrected or meta.get('report_schema_version')!=2 or result['pipeline_version']!=PIPELINE_VERSION:
        old=f'<div class="historical-note"><p>Saved with earlier inspection rules. Original findings are preserved.</p><form method="post" action="/reinspect" enctype="multipart/form-data"><input type="hidden" name="source_inspection_id" value="{identity}"><input type="hidden" name="submission_id" value="{uuid4()}"><button>Reinspect this photo</button></form></div>'
        if corrected:
            measured=esc(provenance.get('measurement_version') or 'not recorded')
            text=(f'<strong>Corrected selection</strong><br>Saved measurements retained ({measured}). Profile unchanged. '
                  f'Correction/scoring: {esc(provenance.get("correction_version") or "not recorded")} / {esc(provenance.get("scoring_version") or "not recorded")}.')
            if not provenance:
                text='<strong>Historical derived report</strong><br>Its measurement origin was not recorded. Reinspect the original to establish current measurements.'
            old=old.replace('Saved with earlier inspection rules. Original findings are preserved.',text)
    elif operation=='reinspection':
        old=f'<p class="provenance-note">Reprocessed original · new measurements and profile from {esc(provenance["measurement_version"])}.</p>'
    contexts=''.join(f'<article class="context-item">{region_preview(p,result,identity)}<div><h4>{esc(p.get("label","Detected region"))} · incidental</h4><p>{esc(p.get("intent_reason","Excluded by the saved intended-product selection."))}</p><a class="evidence-link" href="#evidence-{esc(p["finding_ids"][0])}">View retained findings</a></div></article>' for p in incidental)
    context_section=f'<section class="detail-section" id="incidental-context"><h3>Incidental context · {len(incidental)}</h3><p>Retained as scene evidence. These regions do not count as products or affect product scoring.</p><div class="context-list">{contexts}</div></section>' if incidental else ''
    choices=[]
    for p in regions:
        region=p.get('region_id',p['number'])
        title=f'Product {numbers[region]}' if region in numbers else 'Incidental context'
        choices.append(f'<label class="intent-choice"><input type="checkbox" name="intended_{region}" value="yes" {"checked" if p.get("intended",True) else ""}>{region_preview(p,result,identity)}<span>{title}<br>{esc(p.get("label","Detected region"))}</span></label>')
    confirm=f'<form method="post" action="/confirm-coverage" enctype="multipart/form-data"><input type="hidden" name="source_inspection_id" value="{identity}"><input type="hidden" name="submission_id" value="{uuid4()}"><input type="hidden" name="intent_selection" value="yes"><fieldset><legend>Which visible items are intended products?</legend><div class="intent-options">{"".join(choices)}</div></fieldset><button type="submit" name="coverage_confirm" value="yes">Save intended products</button><p>Creates a new report. This confirms intent and coverage; it does not verify mask accuracy or clear quality findings.</p></form>' if regions else '<p>Mark an intended product before confirming coverage.</p>'
    virtual={**result,'segmentation':{**meta,'product_locator':{}}}
    mark=marking_form(virtual).replace('Mark the target product','Mark a missing or merged product')
    override=f'<section class="detail-section"><details id="coverage-question"><summary>Override intended products or confirm coverage</summary><p>Automatic intent uses prominence, framing, and apparent completeness. Dominant cropped products remain eligible. Override the estimate if your intended subject differs.</p>{confirm}<details><summary>Mark a missing or merged product</summary>{mark}</details></details></section>'
    ledger=old+f'<section class="detail-section">{assessment_summary}{coverage_html}<p id="score-explanation">{esc(explanation)}</p></section>'
    if provenance:
        ledger+=f'<section class="detail-section"><h3>Measurement and correction history</h3><pre>{esc(json.dumps(provenance,indent=2))}</pre></section>'
    if score:
        rows=[]
        for c in score['components']:
            links=' '.join(f'<a href="#evidence-{esc(fid)}">{esc(scope(fid))}</a>' if fid in checks else '<span>Not recorded</span>' for fid in c['finding_ids'])
            rows.append(f'<tr><td>{esc(LABELS[c["component"].split(".")[1]])}</td><td>{c["weight"]}</td><td>{c["earned"]}</td><td>{c["deducted"]}</td><td>{c["withheld"]}</td><td>{links or "No product region"}</td></tr>')
        if score.get('verdict_policy'):
            rule=score['verdict_policy']
            threshold=f'{rule["pass_threshold"]}/100' if rule['pass_threshold'] is not None else 'Invalid setting'
            rows.insert(0,f'<tr><td colspan="6">Provisional Pass threshold: {esc(threshold)}. Essential evidence: {esc(rule["essential_checks"])}. Critical defects block Pass. Decision: {esc(score["verdict_reason"])}. Per-check verdict: {esc(score["check_verdict"])}</td></tr>')
        ledger+=f'<section class="detail-section" id="score-evidence"><h3>Score deductions and uncertainty</h3><p>DIP verdict: {esc(score["verdict"])}. Assessed coverage: {score["assessed_coverage_percent"]}% of weighted checks.</p><p>{score["confirmed_deductions"]} points deducted for confirmed findings. {score["uncertainty_withheld"]} withheld for uncertainty. Possible range: {score["possible_range"][0]}–{score["possible_range"][1]}/100. Missing checks earn no credit.</p><p>{esc(score["method"])}</p><div class="evidence-table-wrap"><table class="evidence-table"><thead><tr><th>DIP check</th><th>Weight</th><th>Earned</th><th>Deducted</th><th>Withheld</th><th>Evidence</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div><p>{esc(score["limitation"])}</p></section>'
    if meta.get('timings'):
        ledger+=f'<section class="detail-section" id="processing-time"><h3>Processing time</h3><p>{result["duration_ms"]/1000:.2f} seconds for this inspection. Upload response Server-Timing also measures storage and application request time.</p><pre>{esc(json.dumps(meta["timings"],indent=2))}</pre></section>'
    error_notice=f'<p role="alert" class="notice error">{esc(error)}</p>' if error else ''
    return page('Photo review',f'''<div class="review">{error_notice}<div class="review-top"><div><span class="eyebrow">Photo result</span><h1>Photo review.</h1><p>{esc(record['filename'])}</p></div><div class="review-links"><a href="/report/{identity}.json">Download report</a><a href="/">Inspect another photo</a></div></div><section class="review-overview" id="whole-photo-score"><div class="score-panel"><span class="score-label">Your photo review</span><h2 id="story-headline">{esc(story["headline"])}</h2><div class="result-rating"><div class="score-value" data-quality-score>{score_value}</div>{badge(score['verdict'] if score else result['status'],'verdict')}</div><p id="story-summary">{esc(story["summary"])}</p>{strengths}{primary_html}{correction}</div><figure class="review-photo">{numbered_photo(result,identity)}<figcaption><span>Original photo · {count} intended product{'' if count==1 else 's'}</span><a href="/evidence/{identity}/original.png" target="_blank" rel="noopener">Open original</a></figcaption></figure></section><div class="{content_class}"><section><div class="review-section-title"><h2>Products in this review</h2><span data-product-count="{count}">{count} product{'' if count==1 else 's'} assessed</span></div><div class="product-cards">{''.join(cards) or '<p>No intended products located. Available photo findings remain under Details.</p>'}</div></section>{next_steps}</div><details class="review-details" id="inspection-details"><summary>Details <span>Measurements, photo checks &amp; review history</span></summary>{context_section}{override}{ledger}<section class="detail-section"><h3>Findings and measurements</h3><p>{esc(coverage['reason'])} {esc(coverage['disclosure'])}</p>{findings_html}</section><section class="detail-section"><h3>Masks and calibration</h3><p>{esc(meta['calibration_disclosure'])}</p><div class="evidence"><figure><img src="/evidence/{identity}/mask.png" alt="Combined detection mask, including incidental regions"><figcaption>All detected regions, including context</figcaption></figure><figure><img src="/evidence/{identity}/overlay.png" alt="Estimated detection regions"><figcaption>Estimated masks, not verified outlines</figcaption></figure></div><p class="source" id="explanation-source">Local wording · available immediately</p><a href="/?advanced=1">Advanced manual inspection tools</a></section><p class="review-footnote">DIP evidence determines this provisional assessment. Photo and product checks keep their calibration limits.</p></details></div><script>
document.addEventListener('click',event=>{{
 const anchor=event.target.closest('a[href^="#"]');if(!anchor)return;
 const target=document.getElementById(anchor.getAttribute('href').slice(1));if(!target)return;
 for(let node=target;node;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;
 requestAnimationFrame(()=>{{target.scrollIntoView({{behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'}});}});
}});
fetch('/explanation/{identity}.json').then(r=>r.ok?r.json():Promise.reject()).then(data=>{{
 for(const item of data.items){{
  for(const node of document.querySelectorAll('[data-statement]'))if(node.dataset.findingId===item.finding_id)node.textContent=item.statement;
  for(const node of document.querySelectorAll('[data-fix]'))if(node.dataset.findingId===item.finding_id)node.textContent=item.fix;
 }}
 if(data.score_explanation)document.getElementById('score-explanation').textContent=data.score_explanation;
 if(data.story){{
  document.getElementById('story-headline').textContent=data.story.headline;
  document.getElementById('story-summary').textContent=data.story.summary;
  const strengths=document.querySelector('.story-strengths p');
  if(strengths){{
   strengths.replaceChildren();
   for(const item of data.story.good){{const node=document.createElement('span');node.dataset.storyGood=item.id;node.textContent=item.text;strengths.append(node,document.createTextNode(' '));}}
  }}
  for(const item of data.story.actions)for(const node of document.querySelectorAll('[data-story-action]'))if(node.dataset.storyAction===item.id)node.textContent=item.text;
 }}
 document.getElementById('explanation-source').textContent=data.source==='groq_validated'?'Groq wording validated against backend DIP evidence':'Local wording · '+(data.reason||'backend evidence preserved');
}}).catch(()=>{{}});
</script>''')


def history(rows):
    items=''.join(f'<div class="history-row"><div><a href="/inspection/{esc(r["id"])}">{esc(r["filename"])}</a><p class="subtle">{esc(r["created_at"][:19].replace("T"," "))} UTC</p></div>{badge(r["status"])}</div>' for r in rows)
    return page('Saved inspections',f'<div class="result-head"><div><span class="eyebrow">Your workspace</span><h1>Saved inspections</h1></div><a class="button" href="/">Inspect a photo</a></div><section class="card">{items or "<p>No inspections saved yet.</p>"}</section>')

CSS += '''
.product-blocker{margin:10px 0;padding:10px;border-left:3px solid #c28c36;background:#fff8e9;font-size:12px;overflow-wrap:anywhere}.product-blocker p{font-size:12px;margin:5px 0}.product-blocker a{font-size:12px}.assessment-summary p{font-size:13px}.provenance-note{font-size:13px;margin:12px 0}
'''

CSS += '''
.review-overview{grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);box-shadow:0 14px 50px #233b2c06}.review-top h1{font-size:25px;letter-spacing:-.7px}.score-panel{padding:32px;gap:16px}.score-panel #story-headline{font-size:clamp(28px,3vw,40px);letter-spacing:-1.3px;line-height:1.14;margin:0;color:#153d30}.result-rating{display:flex;align-items:center;gap:22px;flex-wrap:wrap}.score-value{font-size:62px;letter-spacing:-3px}.score-value small{font-size:20px}.score-panel #story-summary{font-size:16px;line-height:1.6}.story-strengths{border-left:3px solid #a8c5ae;padding:0 0 0 13px}.story-strengths .eyebrow{font-size:10px;letter-spacing:.1em}.story-strengths p{font-size:14px;margin-top:5px}.primary-suggestion{padding:0;border:0;background:none}.primary-suggestion .button{padding:11px 17px}.review-content{grid-template-columns:minmax(0,1.2fr) minmax(0,1fr)}.product-card .product-head{margin-bottom:9px}.product-blocker{padding:9px 0 9px 10px;border-left:2px solid #d6b46b;background:#fffaf1;border-radius:0 6px 6px 0}.product-blocker strong{font-size:11px;font-weight:650}.product-blocker p{font-size:13px;line-height:1.55}.product-blocker a{font-size:11px}.product-clear{font-size:13px}.action-card{background:#fff;border-color:#dce6de;padding:25px}.action-card h2{font-size:21px}.action-scope{font-size:10px;letter-spacing:.04em}.action-item h3{font-size:16px}.action-item p{font-size:14px}.review-top p{overflow-wrap:anywhere}.review-details pre{max-width:100%}.product-card{overflow-wrap:anywhere}
@media(max-width:850px){.review-overview{grid-template-columns:1fr}.review-content{grid-template-columns:1fr}.review-photo svg{max-height:300px}.score-panel{padding:25px;gap:12px}.review-photo{padding:16px}.review-overview{grid-template-areas:"score" "photo"}}
@media(max-width:480px){.review-top{margin:14px 0 18px}.review-top h1{font-size:23px}.score-panel{padding:22px;gap:10px}.score-panel #story-headline{font-size:29px;letter-spacing:-.8px}.score-value{font-size:48px;letter-spacing:-2px}.score-value small{font-size:17px}.result-rating{gap:17px}.score-panel #story-summary{font-size:14px}.story-strengths p{font-size:13px}.review-photo svg{max-height:250px}.review-content{gap:24px}.review-photo figcaption{font-size:11px}.score-panel .subtle{font-size:12px}.review-details>summary span{font-size:11px}}
'''

CSS += '''.review-content.no-actions{grid-template-columns:1fr}'''
