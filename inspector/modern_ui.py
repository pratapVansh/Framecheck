"""Responsive one-click upload/results, with evidence and advanced controls kept in Details."""
import html
import json
from uuid import uuid4

from inspector.auto_inspect import CheckResult, aggregate_required as aggregate
from inspector.narration import approved_wording
from inspector.product_forms import marking_form


def esc(value):return html.escape(str(value),quote=True)


CSS='''
:root{font:16px/1.55 Inter,ui-sans-serif,system-ui,sans-serif;color:#172524;background:#f4f6f3;--green:#164b3f;--muted:#65716b;--line:#dfe5de}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0}a{color:var(--green)}header{max-width:1240px;margin:auto;padding:25px 28px;display:flex;justify-content:space-between;align-items:center}header a{text-decoration:none;font-weight:650}.brand{font-size:23px;letter-spacing:-1px;white-space:nowrap}.brand i{display:inline-block;border:3px solid #50796b;width:19px;height:19px;vertical-align:-1px;margin-right:9px;border-radius:5px}nav{display:flex;gap:24px;font-size:14px}main{max-width:1184px;margin:18px auto 65px;padding:0 28px}.hero{text-align:center;padding:38px 10px 24px}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.16em;font-weight:700;color:#557366}h1{font-size:clamp(34px,5vw,60px);line-height:1.1;letter-spacing:-2.6px;margin:15px 0}h2{font-size:22px;letter-spacing:-.6px;margin:0 0 16px}h3{font-size:17px;margin:0}p{margin:8px 0;color:var(--muted)}.lead{max-width:640px;margin:18px auto}.upload-panel{max-width:760px;margin:20px auto}.dropzone{display:block;text-align:center;background:#fff;border:1.5px dashed #9bb3a6;border-radius:24px;padding:54px 24px;cursor:pointer;box-shadow:0 18px 48px #233b2c06}.dropzone.drag{border-color:#164b3f;background:#e6f0e8}.upload-icon{display:grid;place-items:center;width:58px;height:58px;margin:0 auto 18px;background:#eef4ed;border-radius:17px;color:#164b3f;font-size:28px}.button,button{display:inline-block;border:0;border-radius:12px;background:#164b3f;color:#fff;font:inherit;font-weight:650;padding:12px 21px;cursor:pointer;text-decoration:none}.subtle{font-size:13px;color:var(--muted)}.hidden-input{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}.card{background:white;border:1px solid var(--line);border-radius:19px;padding:23px;margin-bottom:18px}.upload-meta{display:flex;justify-content:center;gap:24px;flex-wrap:wrap;margin:17px 0;font-size:13px;color:var(--muted)}.pill{display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border-radius:30px;font-size:12px;font-weight:750;white-space:nowrap}.pass{background:#e2f2e8;color:#225f40}.fail{background:#fde8e5;color:#9c342b}.manual-review{background:#fff0d4;color:#8b5b1b}.disabled{background:#eef0ed;color:#6b756e}.result-head{display:flex;justify-content:space-between;gap:20px;align-items:center;padding:16px 0 20px}.result-head h1{font-size:40px;letter-spacing:-1.6px;margin:8px 0}.verdict{font-size:17px;padding:9px 18px}.summary{max-width:740px}.coverage{border-left:3px solid #b3c6b7;padding:11px 15px;background:#edf3ec;border-radius:0 12px 12px 0;margin:10px 0 22px;font-size:14px;color:#354d40}.coverage.incomplete{background:#fff5e4;border-color:#dca958;color:#755426}.result-grid{display:grid;grid-template-columns:minmax(0,1.03fr) minmax(0,1fr);gap:26px;align-items:start}.photo-panel{position:sticky;top:18px}.photo-panel svg{display:block;width:100%;max-height:650px;background:#f4f5f1;border-radius:14px}.photo-panel .subtle{margin-top:10px}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px}.product-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:11px}.number{background:#164b3f;color:white;display:inline-grid;place-items:center;min-width:29px;height:29px;border-radius:9px;font-weight:750}.finding{display:grid;grid-template-columns:9px 1fr;gap:10px;padding:9px 0;border-bottom:1px solid #f0f2ed}.finding:last-child{border:0}.dot{width:7px;height:7px;border-radius:50%;margin-top:9px;background:#5a9570}.dot.fail{background:#cd6957}.dot.manual-review{background:#c28c36}.finding p{margin:0;font-size:14px;color:#35443c}.ref{font-size:11px;color:#728477;margin-left:5px;text-decoration:none}.fix{padding:11px 0;border-bottom:1px solid #edf0e9;font-size:14px}.fix:last-child{border:0}.fix b{color:#315543;font-size:12px;display:block}.notice{background:#fff4df;border:1px solid #f0d6a2;padding:14px;border-radius:12px;margin:14px 0}.error{background:#fff0ed;border-color:#e7b9ad;color:#9a3427}.details{margin-top:26px}summary{cursor:pointer;font-weight:650;color:#42604d;padding:10px 0;font-size:14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,monospace;background:#f5f6f2;padding:15px;border-radius:10px}input,select{font:inherit;padding:10px;border:1px solid #c9d3c8;border-radius:9px;max-width:100%}button:focus-visible,a:focus-visible,label:focus-within,svg:focus-visible,summary:focus-visible{outline:3px solid #bd994a;outline-offset:4px}footer{max-width:1184px;margin:30px auto;padding:20px 28px;color:var(--muted);font-size:12px}.empty{padding:28px;text-align:center}.evidence{display:grid;grid-template-columns:1fr 1fr;gap:15px}.evidence img{width:100%;max-height:350px;object-fit:contain}.history-row{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 0;border-bottom:1px solid var(--line)}#product-marking svg{width:100%;max-height:380px}#product-marking button{font-size:12px;padding:9px 10px;margin:4px 0}#product-marking p{font-size:13px}#product-marking h2{font-size:18px}.source{font-size:12px;color:var(--muted)}@media(max-width:850px){.result-grid{grid-template-columns:1fr}.photo-panel{position:static}.photo-panel svg{max-height:500px}.result-head{align-items:flex-start}.hero{padding-top:24px}}@media(max-width:480px){header{padding:20px}.brand{font-size:21px}nav{gap:2px;flex-direction:column;align-items:flex-end;font-size:12px}main{padding:0 18px}.card{padding:18px}.dropzone{padding:35px 15px}h1{letter-spacing:-1.4px}.result-head h1{font-size:30px}.result-head{flex-wrap:wrap}.upload-meta{gap:10px}.evidence{grid-template-columns:1fr}}
'''


def page(title,body):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Framecheck</title><style>{CSS}</style></head><body><header><a class="brand" href="/"><i aria-hidden="true"></i>Framecheck</a><nav><a href="/">Inspect a photo</a><a href="/history">Saved inspections</a></nav></header><main>{body}</main><footer>Inspection stays local. Optional explanations use structured findings, never the photo. Pilot results are not marketplace guarantees.</footer></body></html>'''


def badge(status,extra=''):
    return f'<span class="pill {esc(status.lower().replace(" ","-"))} {extra}">{esc(status)}</span>'


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


def numbered_photo(result,identity):
    w,h=result['width'],result['height'];font=max(22,round(w/28));marks=[]
    for product in result['segmentation']['products']:
        x,y,bw,bh=product['bbox'];n=product['number']
        marks.append(f'<a href="#product-{n}"><rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="4" fill="none" stroke="#164b3f" stroke-width="{max(2,w//300)}"/><rect x="{x}" y="{y}" width="{font*1.3}" height="{font*1.3}" rx="5" fill="#164b3f"/><text x="{x+font*.25}" y="{y+font}" font-size="{font}" font-weight="700" fill="white">{n}</text></a>')
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Uploaded photo with numbered product regions"><image href="/evidence/{identity}/original.png" width="{w}" height="{h}"/>{"".join(marks)}</svg>'


def render_result(record, error=None):
    result=json.loads(record['result_json']);identity=esc(result['inspection_id']);meta=result['segmentation']
    wording={item['finding_id']:item for item in approved_wording(result)}
    def item(check):
        finding_id=check['identifier'];text=wording[finding_id]['statement'];state=check['status'].lower().replace(' ','-')
        applicability='' if check.get('required',True) else '<span class="subtle">Advisory / excluded from verdict. </span>'
        return f'<div class="finding"><span class="dot {state}" aria-label="{esc(check["status"])}"></span><div>{applicability}<p data-finding-id="{esc(finding_id)}" data-statement>{esc(text)}<a class="ref" href="#evidence-{esc(finding_id)}" title="{esc(finding_id)}">Evidence</a></p></div></div>'
    photo=[c for c in result['checks'] if c['identifier'].startswith('photo.')]
    cards=['<section class="card"><div class="section-heading"><h2>Photo checks</h2><span class="subtle">Whole photo</span></div>'+''.join(item(c) for c in photo)+'</section>']
    for product in meta['products']:
        local=[c for c in result['checks'] if c['identifier'] in product['finding_ids']]
        intended=product.get('intended',True)
        state=str(aggregate([CheckResult(**c) for c in local])) if intended else 'Disabled'
        label=f' · {esc(product["label"])}' if product.get('label') else ''
        scope='' if intended else '<p class="subtle">Incidental item. Observed findings remain visible but do not affect the verdict.</p>'
        cards.append(f'<section class="card" id="product-{product["number"]}"><div class="product-head"><span class="number">{product["number"]}</span><h3>Product {product["number"]}{label}</h3>{badge(state)}</div>{scope}'+''.join(item(c) for c in local)+'</section>')
    fixes=[]
    for check in result['checks']:
        if check.get('required',True) and check['status'] not in {'Pass','Disabled'}:
            finding_id=check['identifier'];word=wording[finding_id]
            label='Photo' if finding_id.startswith('photo.') else 'Product '+finding_id.split('.')[0].split('-')[-1]
            fixes.append(f'<div class="fix"><b>{label}</b><span data-finding-id="{esc(finding_id)}" data-fix>{esc(word["fix"])}</span><a class="ref" href="#evidence-{esc(finding_id)}">Evidence</a></div>')
    coverage=meta['coverage'];incomplete=not coverage['coverage_complete'];count=len(meta['products'])
    question=''
    virtual={**result,'segmentation':{**meta,'product_locator':{}}}
    mark=marking_form(virtual).replace('Mark the target product','Mark a missing or merged product')
    choices=''.join(f'<label style="display:block"><input type="checkbox" name="intended_{p["number"]}" value="yes" {"checked" if p.get("intended",True) else ""}> Product {p["number"]}{" — "+esc(p["label"]) if p.get("label") else ""}</label>' for p in meta['products'])
    confirm=f'''<form method="post" action="/confirm-coverage" enctype="multipart/form-data"><input type="hidden" name="source_inspection_id" value="{identity}"><input type="hidden" name="submission_id" value="{uuid4()}"><input type="hidden" name="intent_selection" value="yes"><fieldset><legend>Which numbered items are intended products?</legend>{choices}</fieldset><p>Uncheck incidental items. Mark missing products, or mark individual products if a numbered region merges several items, then exclude the merged candidate.</p><p>Confirming the intended items does not verify mask accuracy or clear quality findings.</p><button type="submit" name="coverage_confirm" value="yes">Save intended items and confirm numbering</button></form>''' if count else '<p>Mark at least one intended product before confirming coverage.</p>'
    if incomplete:
        question=f'<section class="card" id="coverage-question"><h2>Confirm intended products</h2><p>All available photo and product findings are shown. Foreground accounting does not establish intended-product coverage.</p>{confirm}<details><summary>Mark a missing or merged product</summary>{mark}</details></section>'
    else:
        question=f'<details class="card"><summary>Change intended items or mark another product</summary>{confirm}{mark}</details>'
    details=''.join(f'<details id="evidence-{esc(c["identifier"])}"><summary>{esc(c["identifier"])} · {esc(c["status"])}</summary><pre>{esc(json.dumps(c,indent=2))}</pre></details>' for c in result['checks'])
    scope_text='Intended-product coverage unconfirmed' if incomplete else ('Intended items confirmed' if coverage.get('user_confirmed') else 'Estimated scan coverage complete')
    error_notice=f'<p role="alert" class="notice error">{esc(error)}</p>' if error else ''
    return page('Inspection result',f'''{error_notice}<div class="result-head"><div><span class="eyebrow">Inspection result</span><h1>Your photo, checked.</h1><p>{count} numbered product region{'' if count==1 else 's'} · DIP verdict</p></div>{badge(result['status'],'verdict')}</div><div class="coverage {'incomplete' if incomplete else ''}"><strong>{scope_text}.</strong> {esc(coverage['reason'])} {esc(coverage['disclosure'])}</div><div class="result-grid"><aside class="photo-panel card">{numbered_photo(result,identity)}<p class="subtle">Numbers match each product's findings. <a href="/evidence/{identity}/original.png" target="_blank" rel="noopener">Open original</a></p><p class="subtle">Pilot calibration is provisional; broad natural-photo validation remains pending.</p><p><a class="button" href="/">Inspect another photo</a></p></aside><div><h2>What we found</h2>{''.join(cards)}<section class="card"><h2>How to fix it</h2>{''.join(fixes) if fixes else '<p>No fixes are suggested for the completed checks.</p>'}</section>{question}</div></div><details class="card details"><summary>Details · masks, scores, thresholds and calibration</summary><p id="explanation-source" class="source">Local wording · available immediately</p><p>{esc(meta['calibration_disclosure'])}</p><p>{esc(record['filename'])} · {result['width']} × {result['height']} pixels</p><div class="evidence"><figure><img src="/evidence/{identity}/mask.png" alt="Inspection mask"><figcaption>Combined mask of all numbered regions</figcaption></figure><figure><img src="/evidence/{identity}/overlay.png" alt="Inspection overlay"><figcaption>Estimated regions</figcaption></figure></div>{details}<p><a href="/report/{identity}.json">Download structured DIP report</a></p><a href="/?advanced=1">Advanced manual inspection tools</a></details><script>
fetch('/explanation/{identity}.json').then(r=>r.ok?r.json():Promise.reject()).then(data=>{{
 for(const item of data.items){{
  for(const node of document.querySelectorAll('[data-statement]'))if(node.dataset.findingId===item.finding_id){{node.firstChild.textContent=item.statement;}}
  for(const node of document.querySelectorAll('[data-fix]'))if(node.dataset.findingId===item.finding_id)node.textContent=item.fix;
 }}
 const reasons={{
  not_configured:'Local wording · Save GROQ_API_KEY in .env, then refresh this result.',
  configuration_unreadable:'Local wording · The server cannot read its .env file.',
  api_http_401:'Local wording · Groq rejected the configured key. Check .env, then refresh.',
  api_http_403:'Local wording · Groq denied access for this key or model.',
  api_http_400:'Local wording · Groq rejected the model or request settings.',
  api_http_429:'Local wording · Groq rate limit reached. No automatic retries.',
  api_network_access_denied:'Local wording · This server cannot connect to Groq because network access is blocked.',
  api_timeout:'Local wording · Groq timed out. DIP findings remain available.',
  api_unavailable:'Local wording · The server could not reach Groq.',
  api_failed_or_unsupported_claim:'Local wording · Groq returned incomplete or unsupported wording; DIP findings are preserved.',
  finding_limit:'Local wording · This report exceeds the explanation-service finding limit. All DIP findings remain available.'
 }};
 document.getElementById('explanation-source').textContent=data.source==='groq_validated'?'Groq wording validated against DIP finding IDs':(reasons[data.reason]||'Local wording · backend findings preserved');
}}).catch(()=>{{}});
</script>''')


def history(rows):
    items=''.join(f'<div class="history-row"><div><a href="/inspection/{esc(r["id"])}">{esc(r["filename"])}</a><p class="subtle">{esc(r["created_at"][:19].replace("T"," "))} UTC</p></div>{badge(r["status"])}</div>' for r in rows)
    return page('Saved inspections',f'<div class="result-head"><div><span class="eyebrow">Your workspace</span><h1>Saved inspections</h1></div><a class="button" href="/">Inspect a photo</a></div><section class="card">{items or "<p>No inspections saved yet.</p>"}</section>')
