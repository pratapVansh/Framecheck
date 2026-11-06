"""One fresh test evaluation; regression/context and resize stress evidence separately."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

from inspector.benchmark import assert_group_splits, image_bytes, read_manifest, summarize_predictions
from inspector.images import decode_image, png_bytes
from inspector.models import InspectionProfile, aggregate
from inspector.pipeline import classify, inspect_image, measure_image
from inspector.profiles import load_profile
from scripts.prepare_quality_revision import DATA, REPORT, ROOT
from scripts.calibrate_quality_revision import check_metrics


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    started=REPORT/'test.started.json'
    assert not started.exists(), 'Fresh test already consumed; do not retune and rerun'
    frozen=json.loads((DATA/'split.freeze.json').read_text())
    assert digest(DATA/'test.jsonl')==frozen['test_sha256']
    assert digest(DATA/'development.jsonl')==frozen['development_sha256']
    test=read_manifest(DATA/'test.jsonl');dev=read_manifest(DATA/'development.jsonl')
    assert_group_splits(dev+test)
    old_test_groups={r['group_id'] for r in read_manifest(ROOT/'data/revision-v2/benchmark-v2.2.jsonl') if r['split']=='test'}
    assert not old_test_groups & {r['group_id'] for r in test}
    profile,targets=load_profile(ROOT/'data/quality-v3-profile.json')
    methods={'demo_legacy_rules':InspectionProfile(),'guarded_quality_v3':profile}
    code={str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'inspector').glob('*.py'))}
    for name in ('calibrate_quality_revision.py','prepare_quality_revision.py','finalize_quality_test.py','evaluate_quality_revision.py'):
        code['scripts/'+name]=digest(ROOT/'scripts'/name)
    final_freeze={'code_sha256':code,'profile_sha256':digest(ROOT/'data/quality-v3-profile.json'),
                  'calibration_sha256':digest(ROOT/'data/quality-v3-profile.calibration.json'),
                  'test_sha256':frozen['test_sha256'],'development_sha256':frozen['development_sha256'],
                  'annotation_review_sha256':digest(REPORT/'test-annotation-review.json'),
                  'policy':'Fail-only detections; unknown labels excluded; true defects referred to Review count as misses. No test retuning.'}
    (REPORT/'final-method.freeze.json').write_text(json.dumps(final_freeze,indent=2))
    started.write_text(json.dumps({'status':'test started after final code/profile/data freeze','records':len(test)}))
    predictions={name:[] for name in methods};per_check={name:{k:[] for k in ('blur','low_contrast','noise','underexposure','overexposure')} for name in methods}
    timing=[]
    for row in test:
        then=time.perf_counter();measured=measure_image(image_bytes(row));timing.append((time.perf_counter()-then)*1000)
        for name,selected in methods.items():
            checks=classify(measured,selected,photo_context=row['photo_context'])
            predictions[name].append({'id':row['id'],'group_id':row['group_id'],'photo_context':row['photo_context'],
                'status':aggregate(checks),'checks':{c.identifier:c.status for c in checks},
                'reasons':{c.identifier:c.reason_code for c in checks},'quality_measurements':measured.quality})
            for check in checks:
                if check.identifier in per_check[name]:per_check[name][check.identifier].append(check)
    result={'fresh_test_records':len(test),'fresh_test_source_groups':len({r['group_id'] for r in test}),
            'validation_targets_met':targets,'baseline_note':'Legacy demo settings evaluated under 0.4; not an executable 0.3 baseline.',
            'methods':{},'measurement_latency_ms':{'median':float(np.median(timing)),'p95':float(np.percentile(timing,95))},
            'limitations':frozen['limitations']+['No fully labelled twelve-check acceptable test images: overall false-reject denominator is unavailable.',
            'Quality F1 is conditional on known labels; noise has no negative reference support. Normal-sized photos are not calibrated.']}
    for name in methods:
        result['methods'][name]={'metrics':summarize_predictions(test,predictions[name]),'quality':{
            key:check_metrics(test,checks,key) for key,checks in per_check[name].items()},'predictions':predictions[name],
            'review_reasons':dict(Counter(reason for row in predictions[name] for reason in row['reasons'].values() if reason))}
    (REPORT/'fresh-test.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    regression=[]
    for path in sorted((DATA/'regression').glob('shoe-*.png')):
        for context in ('isolated_product','model_worn','uncertain'):
            inspection=inspect_image(path.read_bytes(),profile,photo_context=context)
            record={'case':path.name,'context':context,'result':inspection.to_dict()}
            regression.append(record)
            if context=='isolated_product':
                for kind in ('original','mask','overlay'):(REPORT/f'{path.stem}-{kind}.png').write_bytes(getattr(inspection,kind+'_png'))
    (REPORT/'regressions.json').write_text(json.dumps(regression,indent=2))
    stress=[];chosen=[]
    for category in ('apparel','footwear','bags','watches'):
        chosen.extend([r for r in dev if r.get('kind')=='original' and r['split']=='train' and r.get('category')==category
                       and r.get('photo_context')=='isolated_product' and r.get('mask_reviewed') and r.get('mask')][:2])
    assert len(chosen)==8
    for row in chosen:
        source,_=decode_image(image_bytes(row))
        with Image.open(row['mask']) as image:reference=np.array(image.convert('L'))>127
        for longest in (80,128,256,512,1024,1254,1600):
            shape=(round(source.shape[1]*longest/max(source.shape[:2])),round(source.shape[0]*longest/max(source.shape[:2])))
            rgb=cv2.resize(source,shape,interpolation=cv2.INTER_LINEAR)
            measured=measure_image(png_bytes(rgb));ah,aw=measured.analysis.shape[:2]
            expected=cv2.resize(reference.astype(np.uint8),(aw,ah),interpolation=cv2.INTER_NEAREST)>0
            actual=measured.segmentation.mask
            intersection=np.count_nonzero(actual & expected);union=np.count_nonzero(actual | expected)
            assert (aw,ah)==(round(shape[0]*min(1,512/max(shape))),round(shape[1]*min(1,512/max(shape))))
            stress.append({'source':row['id'],'category':row['category'],'native_size':shape,'analysis_size':[aw,ah],
                           'reference_iou':intersection/union if union else 0,'reliable':measured.segmentation.reliable,
                           'bbox':measured.geometry.get('bbox'),'quality':measured.quality,
                           'type':'synthetic resize stress of an existing thumbnail, not a native normal-sized photo'})
    # Approximate AI outline, including the heel-loop hole; it is not a human-verified mask.
    outline=[(93,782),(109,744),(121,694),(133,677),(134,630),(146,567),(142,535),(134,494),(133,457),(139,434),(150,424),(165,425),(176,441),(181,466),(180,491),(187,470),(207,460),(232,470),(280,509),(323,535),(363,539),(415,520),(452,464),(476,407),(487,396),(505,396),(545,417),(583,446),(635,452),(684,479),(734,518),(782,561),(827,587),(856,609),(891,620),(943,628),(1010,632),(1108,630),(1133,641),(1147,665),(1160,693),(1163,733),(1154,768),(1124,800),(1069,831),(1013,853),(947,867),(847,876),(753,879),(644,877),(542,876),(428,874),(319,874),(228,869),(164,851),(119,825),(99,802)]
    reference=Image.new('L',(1254,1254));draw=ImageDraw.Draw(reference);draw.polygon(outline,fill=255)
    draw.polygon([(150,432),(164,432),(174,451),(177,472),(173,491),(166,509),(153,491),(143,466),(142,448)],fill=0)
    reference.save(REPORT/'shoe-1254-AI-reference.png')
    measured=measure_image((DATA/'regression/shoe-1254-original.png').read_bytes())
    expected=cv2.resize(np.array(reference),(512,512),interpolation=cv2.INTER_NEAREST)>127
    actual=measured.segmentation.mask
    physical={'native_photo_groups':1,'reference_method':'AI visual polygon with heel-loop hole; approximate boundary; not human verified',
              'iou':float(np.count_nonzero(actual & expected)/np.count_nonzero(actual | expected)),
              'segmentation_reliable':measured.segmentation.reliable,'normal_photo_quality_support':False}
    (REPORT/'resize-validation.json').write_text(json.dumps({'synthetic_stress':stress,'actual_1254_shoe':physical,
        'limits':'Synthetic upscaling checks implementation stability only; it cannot validate native detail, blur thresholds, or new product categories.'},indent=2))
    assert all(digest(ROOT/name)==value for name,value in code.items())
    print('Fresh test completed once:',len(test),'records;',len(stress),'resize stresses; nine shoe/context regressions.')
    for name in methods:print(name,{key:val for key,val in result['methods'][name]['quality'].items()})


if __name__=='__main__':main()
