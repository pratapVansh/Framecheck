"""Recheck frozen blur rules on available normal-size sources; never fit thresholds here."""
import hashlib
import json
from pathlib import Path
import sqlite3

import cv2
import numpy as np

from inspector.auto_inspect import POLICY,detect,photo_findings,policy,product_findings
from inspector.images import decode_image,png_bytes

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/multi-product-debug/blur-validation'


def sources():
    with sqlite3.connect(ROOT/'data/inspections.sqlite3') as db:
        for name,identity,split in [
            ('shoe','474ac439-9de4-4ec5-b58d-301d6a753d1b','development source reused for regression'),
            ('single-person','e6d143f8-5bd8-4a30-86e7-8cd9fc7d5615','previous controlled validation source reused'),
            ('two-people','d041db85-0eb8-4f7a-98a5-dcbb6135c94d','excluded from original threshold fitting')]:
            raw=db.execute('select original_png from inspections where id=?',(identity,)).fetchone()[0]
            yield name,raw,split
    for name,file,split in [
        ('white-flat','T-shirt.jpg','development source reused for regression'),
        ('graphic-flat','T_Shirt_Blue.jpg','development source reused for regression'),
        ('raglan','Raglan-sleeve_shirt.jpg','development source reused for regression'),
        ('mundu','Mundu_black_shirt.jpg','original holdout used previously for safety guards; not a new independent test'),
        ('white-worn','White_T_shirt.jpg','original holdout reused; not a new independent test'),
        ('rack','commons-rack.jpg','excluded from original threshold fitting')]:
        path=ROOT/'data/public-shirts/originals'/file
        if path.exists():yield name,path.read_bytes(),split


def run():
    OUT.mkdir(parents=True,exist_ok=False)
    frozen=hashlib.sha256(POLICY.read_bytes()).hexdigest();p=policy();rows=[]
    for name,raw,split in sources():
        rgb,_=decode_image(raw)
        assert min(rgb.shape[:2])>=512,'No thumbnail upscales'
        masks,coverage=detect(rgb)
        severe=cv2.GaussianBlur(rgb,(0,0),max(rgb.shape[:2])/35)
        for condition,image in [('original',rgb),('known-severe-gaussian',severe)]:
            checks,q=photo_findings(image,p)
            blur=next(c for c in checks if c.identifier=='photo.blur')
            products=[]
            for number,mask in enumerate(masks,1):
                local,item=product_findings(image,mask,number,p)
                local_blur=next(c for c in local if c.identifier.endswith('.blur'))
                products.append({'number':number,'score':item['measurements'].get('laplacian_sobel_ratio'),
                    'status':str(local_blur.status),'reason':local_blur.reason_code,'usable_pixels':item['measurements'].get('usable_pixels')})
            rows.append({'source':name,'split_provenance':split,'native_size':list(rgb.shape[1::-1]),
                'source_sha256':hashlib.sha256(raw).hexdigest(),'condition':condition,
                'label_provenance':'Original quality is AI-screened/unknown, not human-verified; Gaussian defect is a known synthetic transformation',
                'photo_blur':{'score':q.get('laplacian_sobel_ratio'),'status':str(blur.status),'reason':blur.reason_code},
                'products':products,'mask_provenance':'Unverified locator predictions on original, fixed for matched blur control; no IoU reference',
                'coverage_complete_on_original':coverage['coverage_complete']})
        print(name,'original/blur',[(r['condition'],r['photo_blur']['status']) for r in rows[-2:]],flush=True)
        (OUT/'results.json').write_text(json.dumps({'rows':rows,'policy_sha256':frozen},indent=2),encoding='utf-8')
    assert hashlib.sha256(POLICY.read_bytes()).hexdigest()==frozen
    report={'rows':rows,'policy_sha256':frozen,'thresholds_changed':False,'native_upscales':False,
        'source_groups':len(rows)//2,'categories_available':['apparel','footwear'],'independent_footwear_holdout':False,
        'conclusion':'Insufficient representative human-labelled evidence to change blur thresholds. Keep boundary/unsafe-Fail Reviews; do not retune to make the reported photo Pass.',
        'limits':['No human-confirmed natural blur labels','No independent footwear holdout','Limited category coverage',
            'Original holdout already informed safety guards','Unverified product masks','Synthetic controls do not establish field accuracy']}
    (OUT/'results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':run()
