"""Small product-routing and native-coordinate target-marking controls."""
import html
from uuid import uuid4


def marking_form(result):
    if "product_locator" not in result["segmentation"]:
        return ""
    identity=html.escape(result["inspection_id"],quote=True)
    width,height=result["width"],result["height"]
    return f'''<section class="card" id="product-marking"><h2>Mark the target product</h2><p>Click around the visible product outline, excluding other items and people. Finish with at least three points. Hidden fabric or product parts stay outside the outline.</p><svg id="product-outline" viewBox="0 0 {width} {height}" tabindex="0" role="img" aria-label="Mark product outline. Click points; keyboard arrows move a cursor, Enter adds a point." style="display:block;width:100%;max-height:480px;touch-action:none;cursor:crosshair"><image href="/evidence/{identity}/original.png" width="{width}" height="{height}"/><polygon id="marked-outline" fill="#ffcc0040" stroke="#e09a00" stroke-width="{max(2,width//300)}"/><circle id="outline-cursor" r="{max(4,width//150)}" fill="#e09a00" visibility="hidden"/></svg><form method="post" action="/mark-product" enctype="multipart/form-data" id="mark-product-form"><input type="hidden" name="source_inspection_id" value="{identity}"><input type="hidden" name="submission_id" value="{uuid4()}"><input type="hidden" name="polygon" id="product-polygon"><p id="marking-status" role="status">No outline points yet.</p><button type="button" id="outline-undo">Undo point</button> <button type="button" id="outline-clear">Clear</button> <button type="submit">Inspect marked product</button></form><p class="caption">Saves a new result. This is a chosen inspection region, not a verified reference mask.</p></section>
<script>
const outlineSvg=document.getElementById('product-outline');
let outlinePoints=[];
let outlineCursor=[outlineSvg.viewBox.baseVal.width/2,outlineSvg.viewBox.baseVal.height/2];
function updateOutline(){{
 document.getElementById('marked-outline').setAttribute('points',outlinePoints.map(p=>p.join(',')).join(' '));
 document.getElementById('product-polygon').value=JSON.stringify(outlinePoints);
 document.getElementById('marking-status').textContent=outlinePoints.length+' outline points. At least three required.';
}}
outlineSvg.addEventListener('click',event=>{{
 const point=new DOMPoint(event.clientX,event.clientY).matrixTransform(outlineSvg.getScreenCTM().inverse());
 outlinePoints.push([Math.max(0,Math.min({width-1},point.x)),Math.max(0,Math.min({height-1},point.y))]); updateOutline();
}});
outlineSvg.addEventListener('keydown',event=>{{
 if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Enter'].includes(event.key))return;
 event.preventDefault(); const step=event.shiftKey?20:2;
 if(event.key==='ArrowLeft')outlineCursor[0]-=step;
 if(event.key==='ArrowRight')outlineCursor[0]+=step;
 if(event.key==='ArrowUp')outlineCursor[1]-=step;
 if(event.key==='ArrowDown')outlineCursor[1]+=step;
 outlineCursor=[Math.max(0,Math.min({width-1},outlineCursor[0])),Math.max(0,Math.min({height-1},outlineCursor[1]))];
 const cursor=document.getElementById('outline-cursor'); cursor.setAttribute('cx',outlineCursor[0]);cursor.setAttribute('cy',outlineCursor[1]);cursor.setAttribute('visibility','visible');
 if(event.key==='Enter'){{outlinePoints.push([...outlineCursor]);updateOutline();}}
}});
document.getElementById('outline-undo').onclick=()=>{{outlinePoints.pop();updateOutline();}};
document.getElementById('outline-clear').onclick=()=>{{outlinePoints=[];updateOutline();}};
document.getElementById('mark-product-form').onsubmit=event=>{{if(outlinePoints.length<3){{event.preventDefault();document.getElementById('marking-status').textContent='Mark at least three outline points before continuing.';}}}};
</script>'''


def whole_photo_panel(result):
    whole=result["segmentation"].get("whole_photo")
    if not whole:
        return ""
    q=whole["diagnostics"]
    names={"sharpness":"Laplacian sharpness", "laplacian_sobel_ratio":"Sharpness / edge energy",
           "contrast":"Luminance contrast P95–P5", "noise":"Flat-patch noise estimate",
           "median_luminance":"Median luminance", "dark_fraction":"Dark pixel fraction",
           "bright_fraction":"Clipped bright pixel fraction"}
    rows=[]
    for key,label in names.items():
        value=q.get(key)
        display=f"{value:.4g}" if isinstance(value,(int,float)) else 'Unavailable'
        detail=''
        if value is None:
            detail=q.get(key+'_unavailable_reason', 'Insufficient whole-photo pixel evidence.')
        rows.append(f'<tr><td>{label}</td><td>{display}</td><td>{html.escape(detail)}</td></tr>')
    checks=''.join('<p>'+html.escape(check['identifier'].replace('_',' '))+': <strong>'+html.escape(check['status'])+'</strong>. '+html.escape(check['explanation'])+'</p>' for check in whole['checks'])
    return '<section class="card" id="whole-photo-checks"><h2>Independent whole-photo checks</h2>'+checks+'<p>These full-frame quality measurements run even when the product target is unknown.</p><details><summary>Whole-photo quality measurements</summary><table><tbody>'+''.join(rows)+'</tbody></table><p class="caption">'+html.escape(q['reason'])+'</p></details></section>'
