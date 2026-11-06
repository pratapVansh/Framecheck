"""Evaluate pre-frozen methods once on the same fresh test groups."""
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

from inspector.benchmark import read_manifest, image_bytes, development_digest, summarize_predictions, report_slices, bootstrap_comparison
from inspector.dip_alternative import classify_alternative
from inspector.images import png_bytes
from inspector.models import InspectionProfile, aggregate
from inspector.pipeline import measure_image, classify


def evidence(measured):
    h,w=measured.rgb.shape[:2]
    mask=cv2.resize(measured.segmentation.mask.astype(np.uint8)*255,(w,h),interpolation=cv2.INTER_NEAREST)
    overlay=measured.rgb.copy();selected=mask>0
    overlay[selected]=(overlay[selected]*.65+np.array([0,180,160])*.35).astype(np.uint8)
    image=Image.fromarray(overlay)
    if measured.geometry:
        x,y,bw,bh=measured.geometry['bbox']
        ImageDraw.Draw(image).rectangle((x,y,min(w-1,x+bw-1),min(h-1,y+bh-1)),outline=(230,70,30),width=max(1,round(max(w,h)/200)))
    return {'original':png_bytes(measured.rgb),'mask':png_bytes(mask),'overlay':png_bytes(np.array(image))}


def main():
    destination=Path('reports/revision-phase6');output=destination/'comparison.json'
    if output.exists() or (destination/'started.json').exists():
        raise ValueError('Preserve the final run; do not re-evaluate or tune on this test.')
    frozen_path=Path('reports/revision-phase5/methods.freeze.json')
    frozen=json.loads(frozen_path.read_text())
    for name,digest in frozen['code_sha256'].items():
        assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==digest,name
    manifest=Path(frozen['manifest'])
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()==frozen['manifest_sha256']
    all_rows=read_manifest(manifest)
    assert development_digest(all_rows)==frozen['development_sha256']
    rows=[r for r in all_rows if r['split']=='test']
    development_groups={r['group_id'] for r in all_rows if r['split']!='test'}
    assert len(rows)==337 and all(r['reviewed'] and r['group_id'] not in development_groups for r in rows)
    (destination/'started.json').write_text(json.dumps({'method_freeze_sha256':hashlib.sha256(frozen_path.read_bytes()).hexdigest(),'test_images':len(rows),'policy':'No method changes after this point.'},indent=2))
    assets=destination/'evidence';assets.mkdir()
    methods={};example_html=[]
    for name,spec in frozen['methods'].items():
        profile_path=Path(spec['profile'])
        assert hashlib.sha256(profile_path.read_bytes()).hexdigest()==spec['sha256']
        profile=InspectionProfile.from_dict(json.loads(profile_path.read_text()))
        predictions=[];mask_scores=[];examples=[];counts=Counter()
        for row in rows:
            started=time.perf_counter();m=measure_image(image_bytes(row))
            checks=classify_alternative(m,profile,photo_context=row['photo_context'])[0] if spec['method']=='edge_supported' else classify(m,profile,photo_context=row['photo_context'])
            blobs=evidence(m);duration=(time.perf_counter()-started)*1000
            pred={'id':row['id'],'group_id':row['group_id'],'photo_context':row['photo_context'],'status':aggregate(checks),
                  'checks':{c.identifier:c.status for c in checks},'reason_codes':{c.identifier:c.reason_code for c in checks},'duration_ms':duration}
            predictions.append(pred)
            if row.get('mask_reviewed') and row['photo_context']=='isolated_product':
                truth=np.array(Image.open(row['mask']))>127
                actual=cv2.resize(m.segmentation.mask.astype(np.uint8),(truth.shape[1],truth.shape[0]),interpolation=cv2.INTER_NEAREST).astype(bool)
                union=np.count_nonzero(truth|actual)
                mask_scores.append({'id':row['id'],'group_id':row['group_id'],'iou':float(np.count_nonzero(truth&actual)/union) if union else 1.0})
            acceptable=all(v is False for v in row['labels'].values())
            missed=any(v is True and pred['checks'][d]!='Fail' for d,v in row['labels'].items())
            reasons=[]
            if acceptable and pred['status']=='Fail':reasons.append('False reject')
            if missed:reasons.append('Missed or unresolved defect')
            if pred['status']=='Manual Review':reasons.append('Manual Review')
            if not missed and ((acceptable and pred['status']=='Pass') or any(v is True for v in row['labels'].values())):reasons.append('Correct known-label case')
            for reason in reasons:
                if counts[reason]>=3:continue
                counts[reason]+=1
                key=name+'-'+hashlib.sha256(row['id'].encode()).hexdigest()[:14]
                for kind,blob in blobs.items():(assets/(key+'-'+kind+'.png')).write_bytes(blob)
                item={'id':row['id'],'reason':reason,'original':'evidence/'+key+'-original.png','overlay':'evidence/'+key+'-overlay.png','mask':'evidence/'+key+'-mask.png',
                      'explanations':[c.explanation for c in checks if c.status in {'Fail','Manual Review'}]}
                examples.append(item)
        metrics=summarize_predictions(rows,predictions)
        groups={r['group_id'] for r in mask_scores}
        methods[name]={'specification':spec,'profile':profile.to_dict(),'metrics':metrics,'slices':report_slices(rows,predictions),
                       'predictions':predictions,'manual_review_by_check':{d:{'count':sum(p['checks'][d]=='Manual Review' for p in predictions),'denominator':len(rows)} for d in rows[0]['labels']},
                       'review_reasons':dict(Counter(code for p in predictions for code in p['reason_codes'].values() if code)),
                       'latency_ms':{'median':float(np.median([p['duration_ms'] for p in predictions])),'p95':float(np.percentile([p['duration_ms'] for p in predictions],95)),
                                     'scope':'decode, measurement, classification, native PNG evidence encoding; excludes SQLite, report writes and UI'},
                       'mask_iou':{'count':len(mask_scores),'groups':len(groups),'mean':float(np.mean([v['iou'] for v in mask_scores])),
                                   'group_mean':float(np.mean([np.mean([v['iou'] for v in mask_scores if v['group_id']==g]) for g in groups])),'per_image':mask_scores},
                       'examples':examples,'targets':{'macro_f1_ge_0_75':metrics['macro_f1']>=.75,'recall_ge_0_80':metrics['defect_recall'] is not None and metrics['defect_recall']>=.8,
                                                     'false_reject_le_0_10':metrics['false_reject_rate'] is not None and metrics['false_reject_rate']<=.1,'review_le_0_30':metrics['manual_review_rate']<=.3,
                                                     'positive_test_groups_ge_10':metrics['complete_category_support']}}
        print(name,{k:metrics[k] for k in ['macro_f1','defect_recall','false_reject_rate','manual_review_rate']},flush=True)
    bootstrap=bootstrap_comparison(rows,{name:r['predictions'] for name,r in methods.items()},iterations=1000,seed=42)
    report={'method_freeze_sha256':hashlib.sha256(frozen_path.read_bytes()).hexdigest(),'manifest_sha256':frozen['manifest_sha256'],
            'test_images':len(rows),'test_groups':len({r['group_id'] for r in rows}),
            'annotation_provenance':{'methods':dict(Counter(r['review_method'] for r in rows)),'human_verified_images':sum(bool(r.get('human_verified')) for r in rows),
                                     'unknown_labels':sum(v is None for r in rows for v in r['labels'].values()),'acceptable_groups':len({r['group_id'] for r in rows if all(v is False for v in r['labels'].values())})},
            'methods':methods,'source_group_bootstrap':bootstrap,'default_method_unchanged':True,
            'limitations':['AI-only labels and approximate masks, not human-verified accuracy.','Variants are correlated within source groups.','Only seven acceptable source groups; false-reject uncertainty is wide.',
                          'Cropping referrals are advisory; primary Fail-only cropping recall remains zero.','Unsafe edge-confidence method is a frozen rejected comparison, never approved for runtime.','No tuning after this final evaluation.']}
    output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    table=''.join('<tr><td>'+html.escape(name)+'</td>'+''.join(f'<td>{m["metrics"][key]:.4f}</td>' for key in ['macro_f1','defect_recall','false_reject_rate','manual_review_rate'])+'</tr>' for name,m in methods.items())
    details=''
    for name,m in methods.items():
        per=''.join(f'<tr><td>{d}</td><td>{v["precision"]:.3f}</td><td>{v["recall"]:.3f}</td><td>{v["f1"]:.3f}</td><td>{v["positive_support"]}</td><td>{v["positive_product_groups"]}</td><td>{v["unknown_labels"]}</td></tr>' for d,v in m['metrics']['per_category'].items())
        figures=''.join(f'<figure><img src="{e["original"]}" alt="Original"><img src="{e["overlay"]}" alt="Overlay"><img src="{e["mask"]}" alt="Mask"><figcaption>{html.escape(e["reason"]+": "+e["id"])}</figcaption><details><summary>Why</summary>{html.escape("; ".join(e["explanations"]))}</details></figure>' for e in m['examples'])
        details+=f'<h2>{html.escape(name)}</h2><table><tr><th>Defect</th><th>Precision</th><th>Recall</th><th>F1</th><th>Positive instances</th><th>Groups</th><th>Unknown</th></tr>{per}</table><pre>'+html.escape(json.dumps({k:m[k] for k in ['latency_ms','mask_iou','targets']},indent=2))+'</pre>'+figures
    page='<!doctype html><html lang="en"><meta charset="utf-8"><title>Framecheck fresh-test comparison</title><style>body{font:16px/1.5 system-ui;max-width:1150px;margin:30px auto;padding:20px}table{border-collapse:collapse;width:100%}td,th{padding:8px;border:1px solid #ccc;text-align:left}pre{white-space:pre-wrap}figure{border:1px solid #ddd;padding:12px}figure img{width:30%;height:180px;object-fit:contain}</style><h1>Framecheck: frozen fresh-test comparison</h1><p>337 records from 60 new source groups. Accuracy targets are not guaranteed; AI annotation is not human verification.</p><table><tr><th>Method</th><th>Macro-F1</th><th>Recall</th><th>False rejects</th><th>Manual Review</th></tr>'+table+'</table><h2>Group bootstrap and limitations</h2><pre>'+html.escape(json.dumps({'bootstrap':bootstrap,'limitations':report['limitations']},indent=2))+'</pre>'+details+'</html>'
    (destination/'comparison.html').write_text(page,encoding='utf-8')


if __name__=='__main__':main()
