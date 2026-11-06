"""Phase 4: fixed DIP comparisons and visual evidence on development data only."""
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import cv2
import numpy as np
from PIL import Image, ImageDraw
from sklearn.metrics import roc_auc_score

from inspector.benchmark import read_manifest, image_bytes, summarize_predictions
from inspector.dip_alternative import edge_measurements, classify_alternative
from inspector.models import InspectionProfile, aggregate
from inspector.pipeline import measure_image, classify


def prediction(checks):
    return {"status":aggregate(checks), "checks":{c.identifier:c.status for c in checks}}


def iou(first, second):
    union = np.count_nonzero(first | second)
    return float(np.count_nonzero(first & second) / union) if union else 1.0


def export_stages(row, measured, folder):
    seg = measured.segmentation
    kernel = np.ones((3,3), np.uint8)
    interior = cv2.erode(seg.mask.astype(np.uint8),kernel).astype(bool)
    background = ~cv2.dilate(seg.mask.astype(np.uint8),kernel).astype(bool)
    gray = cv2.cvtColor(measured.analysis,cv2.COLOR_RGB2GRAY)
    edge = cv2.magnitude(cv2.Sobel(gray.astype(np.float32),cv2.CV_32F,1,0),cv2.Sobel(gray.astype(np.float32),cv2.CV_32F,0,1))
    overlay = measured.analysis.copy()
    overlay[seg.mask] = (overlay[seg.mask]*.65+np.array([0,200,140])*.35).astype(np.uint8)
    if seg.bbox:
        x,y,w,h=seg.bbox
        cv2.rectangle(overlay,(x,y),(x+w-1,y+h-1),(230,70,30),1)
    panels = {"Input":measured.analysis, "LAB distance":np.clip(seg.stages['color_distance']*8,0,255).astype(np.uint8),
              "Threshold mask":seg.raw_mask, "Closing":seg.stages['closed'], "Components":seg.stages['components'],
              "GrabCut + edge preservation":seg.mask, "Product interior":interior, "Background sampling":background,
              "Sobel edges":np.clip(edge,0,255).astype(np.uint8), "Final overlay":overlay}
    sheet = Image.new('RGB',(1000,480),'#edf2f5');draw=ImageDraw.Draw(sheet)
    for index,(title,array) in enumerate(panels.items()):
        if array.dtype==bool:array=array.astype(np.uint8)*255
        image=Image.fromarray(array).convert('RGB');image.thumbnail((180,200))
        if image.width<100:image=image.resize((image.width*2,image.height*2),Image.Resampling.NEAREST)
        image.thumbnail((180,200))
        x=(index%5)*200+5;y=(index//5)*240+25
        sheet.paste(image,(x,y));draw.text((x,y-20),title,fill='black')
    name=hashlib.sha256(row['id'].encode()).hexdigest()[:16]+'.png'
    sheet.save(folder/name)
    return {"id":row['id'],"category":row['category'],"file":"stages/"+name}


def main():
    output=Path('reports/revision-phase4')
    if (output/'comparison.json').exists():
        raise ValueError('Preserve the existing development comparison.')
    manifest=Path('data/revision-v2/benchmark-v2.2.jsonl')
    freeze=json.loads(manifest.with_suffix('.freeze.json').read_text())
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()==freeze['benchmark_sha256']
    rows=[r for r in read_manifest(manifest) if r['split'] in {'train','validation'}]
    assert len(rows)==800 and all(r['split']!='test' for r in rows)
    measured=[measure_image(image_bytes(r)) for r in rows]
    profile=InspectionProfile()
    old_profile=InspectionProfile.from_dict(json.loads(Path('data/calibrated-profile.json').read_text()))
    predictions={key:[] for key in ('demo','historical_calibrated','edge_supported')}
    masks=[];features=[];reasons={key:Counter() for key in predictions}
    baseline_signature=[]
    for row,m in zip(rows,measured):
        base=classify(m,profile,photo_context=row['photo_context'])
        legacy=classify(m,old_profile,photo_context=row['photo_context'])
        alternative,d=classify_alternative(m,profile,photo_context=row['photo_context'])
        for key,checks in [('demo',base),('historical_calibrated',legacy),('edge_supported',alternative)]:
            predictions[key].append(prediction(checks))
            reasons[key].update(c.reason_code for c in checks if c.reason_code)
        baseline_signature.append({'id':row['id'],'checks':{c.identifier:c.status for c in base},
                                   'mask_sha256':hashlib.sha256(m.segmentation.mask.tobytes()).hexdigest()})
        features.append({'id':row['id'],'split':row['split'],'labels':row['labels'],
                         'quality_eligible':row['photo_context']=='isolated_product' and m.segmentation.reliable and m.quality.get('usable_pixels',0)>=32 and min(m.rgb.shape[:2])==60 and max(m.rgb.shape[:2])==80,
                         'laplacian':m.quality.get('sharpness'),**d})
        features[-1].update({key:m.quality.get(key) for key in ('contrast','noise','noise_pixels','usable_pixels','median_luminance')})
        features[-1].update(blur_contrast_confounded=m.quality.get('contrast',0)<15,
                            blur_noise_confounded=m.quality.get('noise',0)>profile.max_noise)
        if row.get('mask_reviewed') and row['photo_context']=='isolated_product':
            truth=np.array(Image.open(row['mask']))>127
            truth=cv2.resize(truth.astype(np.uint8),(m.analysis.shape[1],m.analysis.shape[0]),interpolation=cv2.INTER_NEAREST).astype(bool)
            masks.append({'id':row['id'],'group_id':row['group_id'],'split':row['split'],
                          'baseline_eligible':m.segmentation.reliable,'candidate_eligible':d['object_eligible'],
                          'iou':{key:iou(mask,truth) for key,mask in [('raw',m.segmentation.raw_mask),('closed',m.segmentation.stages['closed']),('components',m.segmentation.stages['components']),('refined',m.segmentation.mask)]}})
    metrics={key:{split:summarize_predictions([r for r in rows if r['split']==split],[p for r,p in zip(rows,preds) if r['split']==split])
                  for split in ('train','validation')} for key,preds in predictions.items()}
    validation=[r for r in masks if r['split']=='validation']
    new=[r for r in validation if r['candidate_eligible'] and not r['baseline_eligible']]
    confident=[r for r in validation if r['candidate_eligible']]
    new_groups={r['group_id'] for r in new}
    gate={'at_least_five_new_validation_reference_groups':len(new_groups)>=5,
          'new_confident_reference_masks_iou_at_least_0_70':bool(new) and all(r['iou']['refined']>=.70 for r in new),
          'confident_reference_mean_iou_at_least_0_80':bool(confident) and np.mean([r['iou']['refined'] for r in confident])>=.80,
          'validation_false_rejects_not_increased':metrics['edge_supported']['validation']['false_rejects']<=metrics['demo']['validation']['false_rejects']}
    group_iou={}
    for split in ('train','validation'):
        selected=[r for r in masks if r['split']==split]
        group_iou[split]={stage:float(np.mean([np.mean([r['iou'][stage] for r in selected if r['group_id']==group]) for group in {r['group_id'] for r in selected}])) for stage in ('raw','closed','components','refined')}
    sharpness={}
    for split in ('train','validation'):
        selected=[r for r in features if r['split']==split and r['quality_eligible'] and r['labels']['blur'] is not None]
        sharpness[split]={}
        for method in ('laplacian','tenengrad'):
            subset=[r for r in selected if r[method] is not None]
            y=[int(r['labels']['blur']) for r in subset]
            sharpness[split][method]={'images':len(subset),'groups':len({r['id'].split('__')[0] for r in subset}),
                'blur_rank_auc':float(roc_auc_score(y,[-r[method] for r in subset])) if len(set(y))==2 else None,
                'note':'Threshold-free descriptive feature comparison, not a calibrated classifier. Correlated variants and segmentation affect interpretation.'}
    # Verify decision/mask parity against the archived executable baseline.
    archived=Path('.test-tmp/phase34-archived-baseline').resolve()
    archived.mkdir(exist_ok=False)
    with ZipFile('reports/revision-phase1/baseline.zip') as archive:archive.extractall(archived)
    code='''
import sys,json,hashlib
from pathlib import Path
from inspector.benchmark import read_manifest,image_bytes
from inspector.pipeline import measure_image,classify
from inspector.models import InspectionProfile
rows=[r for r in read_manifest(sys.argv[1]) if r['split'] in {'train','validation'}]
result=[]
for row in rows:
 m=measure_image(image_bytes(row));checks=classify(m,InspectionProfile(),photo_context=row['photo_context'])
 result.append({'id':row['id'],'checks':{c.identifier:c.status for c in checks},'mask_sha256':hashlib.sha256(m.segmentation.mask.tobytes()).hexdigest()})
Path(sys.argv[2]).write_text(json.dumps(result))
'''
    reference=(output/'archived-baseline-signature.json').resolve()
    subprocess.run([sys.executable,'-B','-c',code,str(manifest.resolve()),str(reference)],cwd=archived,check=True)
    assert baseline_signature==json.loads(reference.read_text()),'Baseline decisions or masks changed'
    stages=output/'stages';stages.mkdir()
    examples=[];chosen=set()
    for category in ('apparel','footwear','bags','watches'):
        candidates=sorted([i for i,r in enumerate(rows) if r['category']==category and r['kind']=='original' and r.get('mask_reviewed')],key=lambda i:rows[i]['id'])[:2]
        chosen.update(candidates)
    for defect in ('background_uniformity','margins','low_contrast','underexposure'):
        chosen.add(next(i for i,r in enumerate(rows) if r.get('transforms') and any(t.get('defect')==defect for t in r['transforms'])))
    for failure in sorted(new, key=lambda item:item['iou']['refined'])[:2]:
        chosen.add(next(i for i,r in enumerate(rows) if r['id']==failure['id']))
    for index in sorted(chosen):examples.append(export_stages(rows[index],measured[index],stages))
    report={'development_images':800,'test_images_used':0,'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'baseline_masks_and_decisions_match_archive':True,'metrics':metrics,'reason_counts':{k:dict(v) for k,v in reasons.items()},
            'group_mean_mask_iou':group_iou,'mask_comparisons':masks,'feature_comparison':sharpness,'features':features,
            'promotion_gate':{k:bool(v) for k,v in gate.items()},'promotable':bool(all(gate.values())),
            'new_confident_mask_failures':[r for r in new if r['iou']['refined']<.70],
            'new_validation_reference_groups':len(new_groups),'examples':examples,
            'limitations':['AI labels and approximate masks; no human verification.',
                          'Historical calibrated cutoffs are a diagnostic reference, not a compatible validated runtime profile.',
                          'No threshold calibration or final method selection occurred; those belong to Phase 5.',
                          'Candidate remains offline regardless of results until explicit calibration/freeze.',
                          'Native dimension and context safeguards remain unchanged. Tenengrad can increase with segmentation edge leakage.']}
    (output/'comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    figures=''.join(f'<figure><img style="max-width:100%" src="{e["file"]}"><figcaption>{html.escape(e["id"])}</figcaption></figure>' for e in examples)
    overview={k:report[k] for k in ('development_images','test_images_used','group_mean_mask_iou','feature_comparison','promotion_gate','promotable','limitations')}
    (output/'comparison.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Development DIP comparison</title><body style="font:16px system-ui;max-width:1100px;margin:30px auto"><h1>Development DIP comparison</h1><p>Training and validation only; no fresh test predictions.</p><pre style="white-space:pre-wrap">'+html.escape(json.dumps(overview,indent=2))+'</pre>'+figures+'</body></html>',encoding='utf-8')
    print(json.dumps(overview,indent=2))


if __name__=='__main__':
    main()
