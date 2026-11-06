"""Provisional normal-resolution controlled-defect calibration with source-group holdout."""
from io import BytesIO
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from inspector.auto_inspect import FEATURE_VERSION, signal, judge
from inspector.images import decode_image, png_bytes
from inspector.regions import analysis_image
from inspector.segmentation import segment

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/one-click-redesign/calibration'
DATA=ROOT/'data/one-click-calibration'


def sources():
    inventory=json.loads((ROOT/'reports/one-click-redesign/normal-source-inventory.json').read_text())
    shoe=next(r for r in inventory if 'running' in r['filename'].lower())
    return [
        ('shoe','footwear','train',ROOT/'reports/one-click-redesign/source-audit'/(shoe['id']+'.png')),
        ('white-flat','apparel','train',ROOT/'data/public-shirts/originals/T-shirt.jpg'),
        ('blue-flat','apparel','train',ROOT/'data/public-shirts/originals/T_Shirt_Blue.jpg'),
        ('raglan','apparel','train',ROOT/'data/public-shirts/originals/Raglan-sleeve_shirt.jpg'),
        ('mundu','apparel','test',ROOT/'data/public-shirts/originals/Mundu_black_shirt.jpg'),
        ('white-worn','apparel','test',ROOT/'data/public-shirts/originals/White_T_shirt.jpg'),
    ]


def controls(rgb):
    random=np.random.default_rng(861)
    return {'clear':rgb,
        'blur':cv2.GaussianBlur(rgb,(0,0),max(rgb.shape[:2])/35),
        'noise':np.clip(rgb.astype(float)+random.normal(0,65,rgb.shape),0,255).astype(np.uint8),
        'contrast':np.clip((rgb.astype(float)-127)*.025+127,0,255).astype(np.uint8),
        'shadow_loss':(rgb.astype(float)*.015).astype(np.uint8),
        'highlight_loss':np.clip(252+rgb.astype(float)*.012,0,255).astype(np.uint8)}


def run():
    OUT.mkdir(parents=True,exist_ok=False);DATA.mkdir(parents=True,exist_ok=False)
    rows=[]
    for identity,category,split,path in sources():
        rgb,_=decode_image(path.read_bytes())
        assert min(rgb.shape[:2])>=512,'No thumbnail upscales in calibration.'
        small=analysis_image(rgb)
        seg=segment(small)
        mask=cv2.resize(seg.mask.astype(np.uint8),rgb.shape[1::-1],interpolation=cv2.INTER_NEAREST).astype(bool)
        for name,image in controls(rgb).items():
            dest=DATA/f'{identity}-{name}.png'
            # Extreme controls can produce PNGs above the upload cap; source dimensions remain genuine.
            dest.write_bytes(png_bytes(image))
            q=signal(image);product=signal(image,mask)
            rows.append({'id':identity+'-'+name,'source_group':identity,'category':category,'split':split,
                'source':str(path.relative_to(ROOT)),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'image':str(dest.relative_to(ROOT)),'size':list(image.shape[1::-1]),'condition':name,
                'label_provenance':'AI-screened original; exact synthetic severe transformation labels, not human natural-defect labels',
                'product_mask_provenance':'Unverified DIP foreground mask on original, held fixed for all derivatives; not an IoU reference',
                'photo':q,'product':product})
    keys={'blur':'laplacian_sobel_ratio','noise':'noise','contrast':'contrast','shadow_loss':'shadow_loss','highlight_loss':'highlight_loss'}
    metrics={};fit={}
    for scope in ['photo','product']:
        for name in (keys if scope=='photo' else ['blur','noise']):
            key=keys[name]
            good=[r[scope][key] for r in rows if r['split']=='train' and r['condition']=='clear' and r[scope].get(key) is not None]
            bad=[r[scope][key] for r in rows if r['split']=='train' and r['condition']==name and r[scope].get(key) is not None]
            low=name in {'blur','contrast'}
            pass_bound=min(good) if low else max(good)
            bad_bound=max(bad) if low else min(bad)
            separated=bad_bound<pass_bound if low else bad_bound>pass_bound
            rule={'direction':'low' if low else 'high','pass':float(pass_bound),'fail':float((pass_bound+bad_bound)/2)} if separated else None
            if rule:metrics[scope+'.'+name]=rule
            fit[scope+'.'+name]={'separated_on_development':separated,'clear':good,'controlled_defects':bad}
    profile={'name':'Normal-resolution DIP pilot','feature_version':FEATURE_VERSION,'metrics':metrics,
        'calibration_kind':'controlled_source_group_holdout_provisional','source_categories':['apparel','footwear'],
        'disclosure':'Provisional pilot: genuine normal-size sources, AI-screened clear labels and synthetic severe defects. Independent natural-defect and broad category validation remain pending.',
        'source_groups_train':sorted({r['source_group'] for r in rows if r['split']=='train'}),
        'source_groups_test':sorted({r['source_group'] for r in rows if r['split']=='test'}),
        'validation_category_gap':'No independent normal-resolution footwear group is available in the holdout.'}
    assert not set(profile['source_groups_train']) & set(profile['source_groups_test'])
    # Freeze thresholds before examining the source-disjoint holdout; validation never modifies metrics.
    (DATA/'policy-frozen-before-validation.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    validation=[]
    for row in rows:
        if row['split']!='test':continue
        predictions={}
        for metric,key in keys.items():
            check=judge('photo.'+metric,row['photo'].get(key),metrics.get('photo.'+metric))
            predictions[metric]=str(check.status)
        validation.append({'id':row['id'],'condition':row['condition'],'predictions':predictions})
    report={'status':'provisional_controlled_validation_not_general_field_accuracy','fit':fit,'validation':validation,
        'natural_defects_human_verified':0,'development_groups':4,'holdout_groups':2,
        'conditions_per_source':6,'native_sources_upscaled':False,'policy_sha256':hashlib.sha256(json.dumps(profile,sort_keys=True).encode()).hexdigest()}
    (DATA/'manifest.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    (OUT/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    (ROOT/'data/one-click-policy.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
    print(json.dumps({'metrics':metrics,'validation':validation,'limits':profile['validation_category_gap']}))


if __name__=='__main__':run()
