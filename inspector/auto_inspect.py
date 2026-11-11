"""One-click DIP inspection. Detection coverage and every required check contribute to the verdict."""
from dataclasses import dataclass, fields
from collections import OrderedDict
import copy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import time
import threading
from uuid import uuid4

import cv2
import numpy as np
from PIL import Image

from inspector.images import decode_image, png_bytes
from inspector.models import CheckResult as LegacyCheckResult, InspectionResult as LegacyInspectionResult
from inspector.models import InspectionProfile, PhotoContext, Status, aggregate
from inspector.product_locator import polygon_mask
from inspector.product_measurements import frame_geometry, measure_scope
from inspector.regions import analysis_image
from inspector.segmentation import segment
from inspector.shirt_locator import CHECKPOINT, MODEL_LOCK, _model
from inspector.scoring import DEFAULT_VERDICT_POLICY, score_photo, score_policy

POLICY = Path(__file__).resolve().parents[1] / 'data/one-click-policy.json'
FEATURE_VERSION = 'normal-dip-v1'
PERSON_LABELS=(2,11,12,13,14,15)
APPAREL_LABELS={1:'Hat',3:'Sunglasses',4:'Top',5:'Skirt',6:'Trousers',7:'Dress',
                8:'Belt',9:'Shoe',10:'Shoe',16:'Bag',17:'Scarf'}
_layout = InspectionProfile()
GROUP_COMPOSITION = {'name':'Declared product-group framing', 'version':'group-framing-v1',
    'source':'Existing demo layout rules applied to the intended group, not individual centers.',
    'calibration':'Declared composition constraints; not marketplace or natural-photo accuracy validation.',
    **{key:getattr(_layout,key) for key in ('min_coverage','max_coverage','max_center_offset','min_margin')}}
PIPELINE_VERSION = '1.9.0-provenance-evidence-dip'
LOCALIZATION_CACHE = OrderedDict()
LOCALIZATION_LOCK = threading.Lock()
LOCALIZATION_CACHE_BYTES = 48 * 1024 * 1024
EVIDENCE_CACHE = OrderedDict()
EVIDENCE_LOCK = threading.Lock()


def detected_regions(meta):
    """Read current partitions and historical mixed product lists without rewriting history."""
    return sorted(meta.get('products',[])+meta.get('incidental_regions',[]),
                  key=lambda p:p.get('region_id',p['number']))


def partition_regions(meta, regions):
    """Keep stable evidence identities separate from consecutive intended-product numbers."""
    intended=[];incidental=[]
    for region in regions:
        region.setdefault('region_id',region['number'])
        region['number']=len(intended)+1 if region.get('intended',True) else None
        (intended if region.get('intended',True) else incidental).append(region)
    meta.update(products=intended,incidental_regions=incidental,report_schema_version=2,
                product_count=len(intended),incidental_count=len(incidental),detection_count=len(regions))


# Applicability belongs to the new flow; frozen legacy model hashes must remain valid.
@dataclass
class CheckResult(LegacyCheckResult):
    required: bool = True


def aggregate_required(checks):
    return aggregate([c for c in checks if getattr(c,'required',True)])


class InspectionResult(LegacyInspectionResult):
    def score(self):
        return score_photo([c.to_dict() for c in self.checks],self.segmentation['products'],aggregate_required(self.checks),
            verdict_policy=self.profile.get('score_verdict',DEFAULT_VERDICT_POLICY))

    def to_dict(self):
        payload=super().to_dict()
        payload['quality_score']=self.score()
        return payload

    @property
    def status(self):
        return self.score()['verdict']

    @property
    def quality_status(self):
        return aggregate_required([c for c in self.checks if c.group=='quality'])

    @property
    def compliance_status(self):
        return aggregate_required([c for c in self.checks if c.group=='compliance'])


def policy():
    try:
        value = json.loads(POLICY.read_text(encoding='utf-8'))
        if value['feature_version'] != FEATURE_VERSION:
            raise ValueError('Incompatible feature version')
        valid={}
        for name,rule in value.get('metrics',{}).items():
            if (isinstance(rule,dict) and rule.get('direction') in {'low','high'} and
                all(type(rule.get(k)) in {int,float} and math.isfinite(rule[k]) for k in ['pass','fail']) and
                ((rule['fail']<rule['pass']) if rule['direction']=='low' else (rule['pass']<rule['fail']))):
                valid[name]=rule
        value['metrics']=valid
        value.setdefault('composition',dict(GROUP_COMPOSITION))
        value.setdefault('score_verdict',dict(DEFAULT_VERDICT_POLICY))
        return value
    except (OSError, ValueError, KeyError):
        return {'name':'Uncalibrated DIP', 'metrics':{}, 'calibration_kind':'unavailable'}


def signal(rgb, mask=None):
    mask = np.ones(rgb.shape[:2], bool) if mask is None else mask
    q = measure_scope(rgb, mask).quality
    q['shadow_loss'] = max(0., 8-q['luminance_p95']) if 'luminance_p95' in q else None
    q['highlight_loss'] = max(0., q['luminance_p05']-247) if 'luminance_p05' in q else None
    return q


def components(mask):
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    minimum = max(24, round(mask.size*.002))
    kept=[i for i in range(1,len(stats)) if stats[i,cv2.CC_STAT_AREA]>=minimum]
    kept.sort(key=lambda i:(int(stats[i,cv2.CC_STAT_LEFT]),int(stats[i,cv2.CC_STAT_TOP])))
    discarded=int(sum(stats[i,cv2.CC_STAT_AREA] for i in range(1,len(stats)) if i not in kept))
    return [labels==i for i in kept], discarded


def separate_foreground(regions, seg):
    """Propose raw-gap separation; silhouettes and narrow joins cannot establish item identity."""
    separated=[];evidence=[]
    raw=getattr(seg,'raw_mask',seg.mask)
    for region in regions:
        parts,_=components(raw&region)
        substantial=[p for p in parts if p.sum()>=region.sum()*.2]
        boxes=[cv2.boundingRect(p.astype(np.uint8)) for p in substantial]
        disjoint=any(all(a[axis]+a[axis+2]<=b[axis] or b[axis]+b[axis+2]<=a[axis]
                         for i,a in enumerate(boxes) for b in boxes[i+1:]) for axis in (0,1))
        x,y,w,h=cv2.boundingRect(region.astype(np.uint8))
        # shortcut: broad solid silhouettes may be one long product or touching items; validate on labelled instances before automatic splitting.
        broad_solid=w/max(1,h)>=2 and region.sum()/(w*h)>=.95
        cores,_=components(cv2.erode(region.astype(np.uint8),np.ones((5,5),np.uint8)).astype(bool))
        neck=len([c for c in cores if c.sum()>=region.sum()*.2])>1
        split=len(substantial)>1 and disjoint and sum(p.sum() for p in substantial)>=region.sum()*.9
        if split:
            separated.extend(substantial)
        else:separated.append(region)
        if split or neck or broad_solid:
            evidence.append({'bbox_analysis':[x,y,w,h],'proposed_parts':len(substantial) if split else 1,
                'reason':'raw_gap' if split else 'narrow_join' if neck else 'broad_solid_silhouette',
                'item_count_verified':False})
    separated.sort(key=lambda m:cv2.boundingRect(m.astype(np.uint8))[:2])
    return separated,evidence


def detect(rgb, *, segmentation=None):
    # shortcut: foreground components are not product instances; replace after independent scene/mask validation.
    small=analysis_image(rgb)
    seg=segmentation if segmentation is not None else segment(small)
    foreground_regions, discarded=components(seg.mask)
    foreground_regions,separation=separate_foreground(foreground_regions,seg)
    tall=any((lambda b:b[3]/max(1,b[2])>1.45)(cv2.boundingRect(m.astype(np.uint8))) for m in foreground_regions)
    def foreground(complete=True):
        masks=[cv2.resize(m.astype(np.uint8),rgb.shape[1::-1],interpolation=cv2.INTER_NEAREST).astype(bool) for m in foreground_regions]
        complete=complete and discarded<=max(8,seg.mask.size*.001)
        return masks, {'method':'general_foreground','coverage_complete':complete,
            'reason':'All substantial foreground regions on a plain background were inspected.' if complete else 'Small foreground regions remain unresolved.',
            'disclosure':'Foreground-based coverage is estimated. Small, hidden, touching or low-contrast items can be missed.',
            'region_evidence':'stable foreground on a plain background',
            'item_count_uncertain':bool(separation),'separation_evidence':separation}
    if seg.reliable and foreground_regions and not tall:
        return foreground()
    gray=cv2.cvtColor(small,cv2.COLOR_RGB2GRAY)
    if float(np.percentile(gray,95)-np.percentile(gray,5))<8:
        return [], {'method':'unresolved','coverage_complete':False,
            'reason':'The photo has too little visible tonal detail for reliable product detection.',
            'disclosure':'Whole-photo findings are available; add a clearer original or mark a visible item.',
            'region_evidence':'insufficient visual signal'}
    # Clothing parsing is used only when its own body-part labels support a person scene.
    try:
        with MODEL_LOCK:
            predict,info=_model()
            labels=predict(rgb,return_labels=True)
        small_labels=cv2.resize(labels.astype(np.uint8),small.shape[1::-1],interpolation=cv2.INTER_NEAREST)
        # In this checkpoint 12/13 are legs; 16/17 are Bag/Scarf and must remain product classes.
        body=np.isin(small_labels,PERSON_LABELS)
        candidates=components(small_labels>0)[0]+(foreground_regions if seg.reliable else [])
        person=np.zeros(body.shape,bool)
        for candidate in candidates:
            if (candidate&body).sum()>=max(24,round(candidate.sum()*.025)):
                person|=candidate
        if person.any():
            near_person=cv2.dilate(person.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
            masks=[];descriptions=[];explained=body&near_person;skipped=0;apparel_separation=[]
            for identity,label in APPAREL_LABELS.items():
                regions,lost=components((small_labels==identity)&near_person)
                regions,proposals=separate_foreground(regions,seg)
                apparel_separation.extend({**proposal,'class_id':identity} for proposal in proposals)
                skipped+=lost
                for region in regions:
                    native=cv2.resize(region.astype(np.uint8),rgb.shape[1::-1],interpolation=cv2.INTER_NEAREST).astype(bool)&(labels==identity)
                    if native.any():
                        masks.append(native);explained|=region
                        descriptions.append({'label':label,'source':'person-associated clothing parser','class_id':identity})
            generic_count=0
            if seg.reliable:
                for region in foreground_regions:
                    if not (region&near_person).any():
                        masks.append(cv2.resize(region.astype(np.uint8),rgb.shape[1::-1],interpolation=cv2.INTER_NEAREST).astype(bool))
                        explained|=region;generic_count+=1
                        descriptions.append({'label':'Foreground item','source':'general foreground'})
            generic_separation=[e for e in separation if not near_person[
                e['bbox_analysis'][1]:e['bbox_analysis'][1]+e['bbox_analysis'][3],
                e['bbox_analysis'][0]:e['bbox_analysis'][0]+e['bbox_analysis'][2]].any()]
            residual=seg.mask&~cv2.dilate(explained.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
            unknown=components(residual)[0]
            substantial=max(24,round(residual.size*.002))
            complete=bool(masks and seg.reliable and not unknown and int(residual.sum())<substantial and skipped<=max(8,round(residual.size*.001)) and discarded<=max(8,round(residual.size*.001)))
            order=sorted(range(len(masks)),key=lambda i:cv2.boundingRect(masks[i].astype(np.uint8))[:2])
            masks=[masks[i] for i in order];descriptions=[descriptions[i] for i in order]
            if not masks:reason='A person is visible, but reliable product regions are missing. Mark the intended products.'
            elif not seg.reliable:reason='Detected product regions were inspected; the background does not support a complete coverage assessment.'
            elif not complete:reason='Detected product regions were inspected, but unexplained foreground or small product regions remain.'
            else:reason='All substantial foreground is accounted for by inspected product regions and visible person parts.'
            return masks, {'method':'hybrid_foreground_clothing' if generic_count else 'clothing_segformer',
                'coverage_complete':complete,'reason':reason,
                'disclosure':'Coverage is estimated from foreground and supported apparel labels. Hidden, touching, small or low-contrast items may be missed or merged.',
                'region_evidence':'person-associated apparel regions and independently separated foreground items',
                'region_descriptions':descriptions,'unexplained_pixels':int(residual.sum()),'unexplained_regions':len(unknown),
                'discarded_apparel_pixels':int(skipped),'foreground_reliable':seg.reliable,'model':info,
                'item_count_uncertain':bool(apparel_separation or generic_count and generic_separation),
                'separation_evidence':apparel_separation+generic_separation}
        if seg.reliable and foreground_regions:
            return foreground()
    except (ImportError,OSError,RuntimeError,ValueError,KeyError,TypeError):
        pass
    if seg.reliable and foreground_regions:
        candidates=[m for m in foreground_regions if (lambda b:b[3]/max(1,b[2])<=1.45)(cv2.boundingRect(m.astype(np.uint8)))]
        masks=[cv2.resize(m.astype(np.uint8),rgb.shape[1::-1],interpolation=cv2.INTER_NEAREST).astype(bool) for m in candidates]
        _,coverage=foreground(False)
        coverage['reason']='Separated foreground items were inspected; unclassified tall regions may include people or held items. Mark missing products.'
        return masks,coverage
    return [], {'method':'unresolved','coverage_complete':False,
        'reason':'The scene does not establish reliable product regions. Mark a visible product to complete its checks.',
        'disclosure':'No complete detection claim is made. Whole-photo findings remain available.',
        'region_evidence':'insufficient foreground or clothing evidence'}


def localize(rgb, input_hash):
    """Reuse only localization, never prior scores; bound native masks and analysis arrays in memory."""
    signature=[]
    for name in ('config.json','preprocessor_config.json','model.safetensors'):
        path=CHECKPOINT/name
        try:stat=path.stat()
        except OSError:stat=None
        signature.append((name,stat.st_size,stat.st_mtime_ns) if stat else (name,None))
    key=(input_hash,rgb.shape,PIPELINE_VERSION,detect,segment,_model,tuple(signature))
    with LOCALIZATION_LOCK:
        if key in LOCALIZATION_CACHE:
            value,_=LOCALIZATION_CACHE[key];LOCALIZATION_CACHE.move_to_end(key)
            return (*copy.deepcopy(value),True)
        seg=segment(analysis_image(rgb))
        masks,coverage=detect(rgb,segmentation=seg)
        value=(masks,coverage,seg)
        size=sum(m.nbytes for m in masks)+seg.mask.nbytes+getattr(seg,'raw_mask',seg.mask).nbytes
        size+=sum(a.nbytes for a in getattr(seg,'stages',{}).values())
        # Unresolved/model-unavailable results must be retried rather than cached as permanent failures.
        if coverage.get('coverage_complete') and size<=LOCALIZATION_CACHE_BYTES:
            LOCALIZATION_CACHE[key]=(copy.deepcopy(value),size)
            while len(LOCALIZATION_CACHE)>4 or sum(v[1] for v in LOCALIZATION_CACHE.values())>LOCALIZATION_CACHE_BYTES:
                LOCALIZATION_CACHE.popitem(last=False)
        return (*value,False)


FIXES={
    'resolution':'Upload a larger original photo; resizing a small image will not restore detail.',
    'contrast':'Use a contrasting background and lighting that reveals the product.',
    'blur':'Refocus, keep the camera steady, and take another photo.',
    'noise':'Use more light and a lower ISO, then take another photo.',
    'shadow_loss':'Increase light or exposure until dark detail is visible.',
    'highlight_loss':'Reduce exposure or soften the light until bright detail returns.',
    'detail':'Move closer or upload an original with more product pixels.',
    'edge':'Leave space around the product and confirm that no part is cut off.',
    'coverage':'Check that every product you want assessed is numbered. Mark missing or touching products, then save your selection. This does not verify outline accuracy.',
    'foreground_coverage':'Inspect the unaccounted areas. Mark intended products; exclude incidental regions in the intended-item controls.',
    'clipping':'Inspect the original product detail. Natural white or black material can resemble clipping; adjust lighting only if detail is lost.',
    'mask_completeness':'Check the full product outline, including flat bright or dark areas. Mark a fuller region and exclude an incomplete candidate.',
    'group_size':'Move closer or step back so the products fill the photo while keeping their edges visible.',
    'group_placement':'Move the products together nearer the center of the photo.',
    'group_margins':'Leave a clear gap between the products and the photo edges.'}


def finding(identifier, status, explanation, values=None, thresholds=None, reason=None, *, required=True):
    return CheckResult(identifier,'compliance' if identifier.endswith(('coverage','edge','mask_completeness','group_size','group_placement','group_margins')) else 'quality',
        Status(status),values or {},thresholds or {},explanation,
        'original' if identifier.startswith('photo.') else 'overlay',reason if status=='Manual Review' else None,required)


def judge(identifier, value, rule, *, signal_ok=True):
    label=identifier.rsplit('.',1)[-1]
    scope='The photo' if identifier.startswith('photo.') else 'This product region'
    if value is None or not signal_ok:
        return finding(identifier,'Manual Review',f'{scope} does not have enough measurable detail for the {label.replace("_"," ")} check.',
            {'score':value},reason='insufficient_signal')
    if not rule:
        return finding(identifier,'Manual Review',f'The {label.replace("_"," ")} measurement is available, but a compatible threshold is not calibrated.',
            {'score':value},reason='calibration_unavailable')
    bad_low=rule['direction']=='low'
    failed=value<=rule['fail'] if bad_low else value>=rule['fail']
    passed=value>=rule['pass'] if bad_low else value<=rule['pass']
    if failed and rule.get('fail_validated') is False:
        return finding(identifier,'Manual Review',f'The {label.replace("_"," ")} score is weak, but this Fail range did not validate on separate clear-photo controls.',
            {'score':float(value)},rule,'fail_region_unvalidated')
    status='Fail' if failed else 'Pass' if passed else 'Manual Review'
    phrases={
        'blur':('Fine detail is weaker than the pilot threshold.','The sharpness check met the pilot threshold.'),
        'contrast':('The photo has too little tonal separation.','The contrast check met the pilot threshold.'),
        'noise':('The noise estimate exceeds the pilot limit.','The noise check met the pilot threshold.'),
        'shadow_loss':('Most dark detail is too weak to inspect.','The dark-detail check met the pilot threshold.'),
        'highlight_loss':('Most highlight detail is too weak to inspect.','The highlight-detail check met the pilot threshold.')}
    fail,good=phrases[label]
    text=fail if failed else good if passed else (f'{scope} sharpness falls in the pilot uncertainty band; this check cannot decide sharpness reliably.' if label=='blur' else f'The {label.replace("_"," ")} score lies between the supported Pass and Fail regions.')
    return finding(identifier,status,text,{'score':float(value)},rule,'calibration_boundary' if status=='Manual Review' else None)


def photo_findings(rgb, p):
    q=signal(rgb)
    h,w=rgb.shape[:2]
    checks=[finding('photo.resolution','Pass' if min(h,w)>=256 and max(h,w)>=384 else 'Fail',
        'The original has enough pixels for this inspection.' if min(h,w)>=256 and max(h,w)>=384 else 'The original is too small for this inspection.',
        {'width':w,'height':h},{'minimum_short_side':256,'minimum_long_side':384})]
    metrics={'blur':'laplacian_sobel_ratio','contrast':'contrast','noise':'noise','shadow_loss':'shadow_loss','highlight_loss':'highlight_loss'}
    for name,key in metrics.items():
        adequate=q.get('usable_pixels',0)>=32
        if name=='blur':
            adequate=adequate and q.get('contrast',0)>=15 and q.get('noise') is not None
            noise_rule=p.get('metrics',{}).get('photo.noise')
            if noise_rule and (q.get('noise') is None or q['noise']>=noise_rule['fail']):adequate=False
        checks.append(judge('photo.'+name,q.get(key),p.get('metrics',{}).get('photo.'+name),signal_ok=adequate))
    return checks,q


def mask_evidence(rgb, mask):
    """Flag detached extreme-tone areas beside a retained region, without claiming missing parts."""
    small=analysis_image(rgb)
    local=cv2.resize(mask.astype(np.uint8),small.shape[1::-1],interpolation=cv2.INTER_NEAREST).astype(bool)
    gray=cv2.cvtColor(small,cv2.COLOR_RGB2GRAY)
    extreme=((gray>=250)|(gray<=5))&~local
    count,labels,stats,_=cv2.connectedComponentsWithStats(extreme.astype(np.uint8),8)
    near=cv2.dilate(local.astype(np.uint8),np.ones((5,5),np.uint8)).astype(bool)
    suspects=[]
    for i in range(1,count):
        x,y,w,h,area=map(int,stats[i])
        if area<max(24,local.sum()*.1) or x==0 or y==0 or x+w==gray.shape[1] or y+h==gray.shape[0]:continue
        if (near&(labels==i)).any():suspects.append([x,y,w,h])
    return {'adjacent_extreme_regions':suspects,'analysis_size':list(small.shape[1::-1]),
            'mask_verified':False,'calibration':'Unvalidated suspicion trigger; never proves missing content.'}


def product_findings(rgb, mask, number, p):
    q=signal(rgb,mask)
    bbox=[int(v) for v in cv2.boundingRect(mask.astype(np.uint8))]
    prefix=f'product-{number}'
    enough=q.get('usable_pixels',0)>=32 and min(bbox[2:])>=64
    checks=[finding(prefix+'.detail','Pass' if enough else 'Manual Review',
        'Enough product pixels are available for local inspection.' if enough else 'This product is too small for reliable local measurements.',
        {'usable_pixels':q.get('usable_pixels',0),'bbox':bbox},{'minimum_native_extent':64},'insufficient_product_pixels' if not enough else None)]
    for name,key in [('blur','laplacian_sobel_ratio'),('noise','noise')]:
        adequate=enough and (name!='blur' or q.get('contrast',0)>=15)
        noise_rule=p.get('metrics',{}).get('product.noise')
        if name=='blur' and noise_rule and (q.get('noise') is None or q['noise']>=noise_rule['fail']):adequate=False
        checks.append(judge(prefix+'.'+name,q.get(key),p.get('metrics',{}).get('product.'+name),signal_ok=adequate))
    h,w=mask.shape
    edge_distance=min(bbox[0]/w,bbox[1]/h,(w-bbox[0]-bbox[2])/w,(h-bbox[1]-bbox[3])/h)
    contact=edge_distance<=.005
    checks.append(finding(prefix+'.edge','Manual Review' if contact else 'Pass',
        'The region reaches or nearly touches the photo edge; missing product parts cannot be ruled out.' if contact else 'No product contact with the photo edge was found.',
        {'edge_contact':contact,'nearest_edge_fraction':edge_distance},{'edge_tolerance_fraction':.005},reason='possible_cropping' if contact else None))
    # The existing blur signal floor is an evidence guard, not a new quality cutoff.
    tonal=enough and q.get('contrast',0)>=15
    checks.append(finding(prefix+'.contrast','Pass' if tonal else 'Manual Review',
        'Local tonal separation supports measurement; product contrast quality is not separately calibrated.' if tonal else
        'Local tonal detail is weak or unavailable. Natural uniform material and lost detail cannot be distinguished reliably.',
        {'score':q.get('contrast')},{'minimum_signal_contrast':15,'quality_cutoff_calibrated':False},
        'local_contrast_unvalidated' if not tonal else None))
    clipped=not enough or q.get('dark_fraction',1)>=_layout.max_dark_fraction or q.get('bright_fraction',1)>=_layout.max_bright_fraction
    checks.append(finding(prefix+'.clipping','Manual Review' if clipped else 'Pass',
        'Large dark or bright areas need review; natural black or white material can resemble clipping. Local exposure cutoffs are unvalidated.' if clipped else
        'No large dark/bright-area warning was found; this is not a calibrated local exposure-quality decision.',
        {key:q.get(key) for key in ('dark_fraction','bright_fraction','shadow_loss','highlight_loss')},
        {'review_fraction':_layout.max_dark_fraction,'quality_cutoff_calibrated':False},
        'local_clipping_unvalidated' if clipped else None))
    evidence=mask_evidence(rgb,mask)
    suspect=bool(evidence['adjacent_extreme_regions'])
    checks.append(finding(prefix+'.mask_completeness','Manual Review' if suspect else 'Pass',
        'A flat bright/dark area beside this mask may belong to the product. The retained mask may be incomplete; confirm the full outline.' if suspect else
        'No adjacent extreme-tone mask warning was found. Mask accuracy remains unverified.',
        evidence,reason='suspected_incomplete_mask' if suspect else None))
    return checks,{'number':number,'bbox':bbox,'visible_pixels':int(mask.sum()),'finding_ids':[c.identifier for c in checks],
        'measurements':q,'mask_verified':False,'intended':True}


def select_intended(products, checks, width, height):
    """Suggest intent from geometry across categories; never discard detection evidence."""
    by_id={c.identifier:c for c in checks}
    for product in products:
        x,y,w,h=product['bbox']
        edge=by_id[f"product-{product['number']}.edge"].measurement['edge_contact']
        suspect=by_id[f"product-{product['number']}.mask_completeness"].status!=Status.PASS
        product['intent_evidence']={
            'area_fraction':product['visible_pixels']/(width*height),
            'center_offset':max(abs((x+w/2)/width-.5),abs((y+h/2)/height-.5)),
            'short_extent_fraction':min(w/width,h/height),
            'edge_contact':edge,'visible_completeness':'uncertain' if edge or suspect else 'apparently_complete',
        }
    anchors=[p for p in products if p['intent_evidence']['visible_completeness']=='apparently_complete'
             and p['intent_evidence']['area_fraction']>=.03 and p['intent_evidence']['center_offset']<=.35]
    largest=max((p['visible_pixels'] for p in products),default=0)
    anchor_size=max((p['visible_pixels'] for p in anchors),default=0)
    for product in products:
        e=product['intent_evidence'];area=product['visible_pixels']
        e['relative_prominence']=area/largest if largest else 0
        e['continuation_anchor']=None
        if e['edge_contact'] and e['relative_prominence']<.65 and e['short_extent_fraction']<=.40 and e['center_offset']>=.20:
            x,y,w,h=product['bbox'];box=(x/width,y/height,w/width,h/height)
            for anchor in anchors:
                if anchor is product or area>anchor['visible_pixels']*.65:continue
                x,y,w,h=anchor['bbox'];reference=(x/width,y/height,w/width,h/height)
                for axis in (0,1):
                    other=1-axis
                    overlap=min(box[other]+box[other+2],reference[other]+reference[other+2])-max(box[other],reference[other])
                    if (box[other+2]>reference[other+2] or overlap<box[other+2]*.9 or
                            box[axis+2]>reference[axis+2]):continue
                    gaps=[]
                    if box[axis]<=.005:gaps.append(reference[axis]-box[axis]-box[axis+2])
                    if box[axis]+box[axis+2]>=.995:gaps.append(box[axis]-reference[axis]-reference[axis+2])
                    # shortcut: aligned edge continuations suggest context, not identity; use the optional correction until instance labels validate intent.
                    if any(-.05*min(box[axis+2],reference[axis+2])<=gap<=.025 for gap in gaps):
                        e['continuation_anchor']=anchor['number']
                        break
                if e['continuation_anchor'] is not None:break
        incidental=(anchor_size>0 and e['edge_contact'] and area<=anchor_size*.55
                    and e['relative_prominence']<.6 and e['short_extent_fraction']<=.30
                    and e['center_offset']>=.20)
        incidental=incidental or e['continuation_anchor'] is not None
        product.update(intended=not incidental,intent_source='automatic',
            intent_reason='Smaller partial edge region aligned with an apparently complete product; estimated incidental context.' if e['continuation_anchor'] is not None else
                'Small peripheral edge fragment beside a more prominent, apparently complete product.' if incidental else
                'Retained as an intended candidate; dominant or ambiguous cropped products stay assessable.' if e['edge_contact'] else
                'Retained as a visible product candidate based on prominence, framing, and apparent completeness.',
            intent_ambiguous=bool((e['edge_contact'] or e['visible_completeness']=='uncertain') and not incidental))
        for identity in product['finding_ids']:by_id[identity].required=not incidental
    return {'version':'geometry-intent-v2','estimated':True,
        'rule':'Exclude only peripheral edge fragments with area <=55% of a complete anchor, <60% of the largest region, '
               'short extent <=30% of frame, and center offset >=20%; or aligned edge continuations with area <=65% of '
               'a complete anchor and <65% of the largest region, short extent <=40%, and offset >=20%. '
               'Continuations overlap at least 90% across their width, are no larger than the anchor on either axis, '
               'and attach within a 2.5% frame gap or 5% extent overlap. Anchors occupy >=3% of frame with offset <=35%.',
        'limitation':'Geometry suggests intent, not product identity or mask accuracy. User selection overrides this estimate.'}


def coverage_evidence(rgb, masks, coverage, products, *, segmentation=None):
    """Accept supported intended scope while retaining advisory foreground uncertainty."""
    small=analysis_image(rgb)
    seg=segmentation if segmentation is not None else segment(small)
    accounted=seg.mask
    if coverage.get('method')=='general_foreground':
        accounted=np.zeros(seg.mask.shape,bool)
        for mask in masks:
            accounted|=cv2.resize(mask.astype(np.uint8),small.shape[1::-1],interpolation=cv2.INTER_NEAREST).astype(bool)
    near=cv2.dilate(accounted.astype(np.uint8),np.ones((3,3),np.uint8)).astype(bool)
    lost=seg.raw_mask&~near
    stages=getattr(seg,'stages',{})
    distances=stages.get('color_distance')
    weak=np.zeros(seg.mask.shape,bool)
    if distances is not None and seg.reliable:
        weak=(distances>max(1.,seg.diagnostics.get('threshold_delta_e',10)*.1))&~near
    # Small/raw and weak-tone areas are review evidence, not newly accepted product masks.
    def count_regions(mask):
        _,_,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
        return int(sum(area>=24 for area in stats[1:,cv2.CC_STAT_AREA]))
    missing=count_regions(lost|weak)
    # Reuse the detector's substantial-component floor for weak tones; retain raw-region warnings at 24 pixels.
    # Weak-tone accounting supports plain backgrounds, not person-scene background gradients.
    substantial=count_regions(lost)
    if coverage.get('method')=='general_foreground':substantial+=len(components(weak)[0])
    included=[p for p in products if p.get('intended',True)]
    reliable=bool(seg.reliable and coverage.get('coverage_complete') and
        coverage.get('method') in {'general_foreground','clothing_segformer','hybrid_foreground_clothing'})
    if not included:
        reason_code='intended_products_missing'
        reason='No intended product was reliably located. Mark the intended products.'
    elif not reliable:
        reason_code='product_detection_unreliable'
        reason=coverage['reason']+' Detection does not establish complete intended-product coverage.'
    elif substantial:
        reason_code='suspected_missing_regions'
        reason='Substantial unaccounted regions may be missing products. Mark missing products or confirm the intended selection.'
    elif coverage.get('item_count_uncertain'):
        reason_code='item_count_unresolved'
        reason='A connected region may contain touching products or parts of one product. Check the item count; mark separate products if needed.'
    elif any(p.get('intent_ambiguous',True) for p in included):
        reason_code='intended_selection_ambiguous'
        reason='Cropping or an incomplete outline makes the intended selection uncertain. Review the intended products.'
    else:
        reason_code=None
        reason='Reliable detection supports the automatic intended-product selection. Coverage is estimated; mask accuracy remains unverified.'
    complete=reason_code is None
    value=dict(coverage)
    value.update(foreground_complete=bool(coverage['coverage_complete'] and not missing),
        foreground_reason=coverage['reason'],suspected_missing_regions=missing,
        substantial_missing_regions=substantial,detection_reliable=reliable,
        discarded_raw_pixels=int(lost.sum()),intended_complete=complete,user_confirmed=False,
        coverage_complete=complete,mask_verified=False,acceptance_source='automatic' if complete else 'unresolved',
        reason=reason,reason_code=reason_code,
        disclosure=coverage.get('disclosure','')+' Intended-item confirmation does not verify mask accuracy.')
    return value,seg


def coverage_findings(products, coverage):
    included=[p for p in products if p.get('intended',True)]
    complete=bool(included and coverage.get('intended_complete'))
    foreground=coverage.get('foreground_complete',False)
    return [finding('photo.foreground_coverage','Pass' if foreground else 'Manual Review',
        coverage.get('foreground_reason','Foreground accounting is unavailable.')+(
            ' Additional small or weak-tone areas may be missed products.' if coverage.get('suspected_missing_regions') else ''),
        {key:coverage.get(key) for key in ('foreground_complete','suspected_missing_regions','discarded_raw_pixels')},
        reason='suspected_missing_regions' if not foreground else None,required=False),
        finding('photo.coverage','Pass' if complete else 'Manual Review',coverage['reason'],
            {'detected_regions':len(products),'intended_products':len(included),'incidental_regions':len(products)-len(included),
             'user_confirmed':coverage.get('user_confirmed',False),'acceptance_source':coverage.get('acceptance_source','unresolved'),
             'mask_verified':False},reason=coverage.get('reason_code') or ('intended_coverage_unconfirmed' if not complete else None))]


def group_findings(products, width, height, p, *, accepted):
    included=[item['bbox'] for item in products if item.get('intended',True)]
    geometry={}
    if included:
        x=min(b[0] for b in included);y=min(b[1] for b in included)
        right=max(b[0]+b[2] for b in included);bottom=max(b[1]+b[3] for b in included)
        geometry=frame_geometry([x,y,right-x,bottom-y],width,height)
    rule=p.get('composition',{})
    if not isinstance(rule,dict):rule={}
    supported=(isinstance(rule,dict) and rule.get('version')=='group-framing-v1' and
        all(type(rule.get(k)) in {int,float} and math.isfinite(rule[k]) for k in
            ('min_coverage','max_coverage','max_center_offset','min_margin')) and
        0<rule['min_coverage']<rule['max_coverage']<=1 and 0<=rule['max_center_offset']<=.5 and 0<=rule['min_margin']<.5)
    checks=[]
    for name,key,lower,upper in [('group_size','coverage',rule.get('min_coverage'),rule.get('max_coverage')),
            ('group_placement','center_offset',None,rule.get('max_center_offset')),
            ('group_margins','margin_min',rule.get('min_margin'),None)]:
        value=geometry.get(key)
        bad=value is not None and supported and ((lower is not None and value<lower) or (upper is not None and value>upper))
        reason=None
        if not supported:reason='composition_policy_unavailable'
        elif value is None:reason='intended_group_missing'
        elif not accepted:reason='intended_group_unconfirmed'
        elif name=='group_size' and value<lower:reason='small_in_frame'
        elif any(abs(value-bound)<=1/min(width,height) for bound in (lower,upper) if bound is not None):reason='geometry_boundary'
        # A declared rule can fail only after reliable detection or the user establishes the intended group.
        state='Manual Review' if reason and (reason!='small_in_frame' or not accepted) else 'Fail' if bad else 'Pass'
        explanations={
            'composition_policy_unavailable':'No supported composition policy is available for this inspection.',
            'intended_group_missing':'No intended product group is available for framing assessment.',
            'small_in_frame':'The intended product group is small in the frame under the declared composition policy.',
            'intended_group_unconfirmed':'Group framing was measured; resolve uncertain intended-product coverage before a composition verdict.',
            'geometry_boundary':'Group framing is within one native pixel of the declared rule boundary.'}
        text=explanations.get(reason,f'The intended product group {"does not meet" if bad else "meets"} the declared {name.replace("group_","")} rule. Individual items need not be centered.')
        dependency={'blocked_by':'photo.coverage'} if reason in {'intended_group_missing','intended_group_unconfirmed'} else {}
        checks.append(finding('photo.'+name,state,text,{'score':value,**geometry,**dependency},
            {'policy':rule,'minimum':lower,'maximum':upper,'native_coordinates':True},reason))
    failed=[c for c in checks if c.status==Status.FAIL]
    severe=len(failed)>=2 or any(c.identifier=='photo.group_size' and
        c.measurement['score']<rule['min_coverage']/2 for c in failed)
    for check in checks:
        check.measurement['severe_framing']=bool(severe and check.status==Status.FAIL)
        check.threshold['severity_rule']='Two framing rules fail, or group extent is below half the declared minimum.'
    return checks


def guard_photo_contrast(checks, rgb, masks, seg):
    union=np.zeros(rgb.shape[:2],bool)
    for mask in masks:union|=mask
    fraction=float(union.mean())
    for check in checks:
        if check.identifier=='photo.contrast' and check.status!=Status.PASS and seg.reliable and 0<fraction<=.05:
            check.status=Status.REVIEW;check.reason_code='background_dominated_contrast'
            check.explanation='Blank background dominates the whole-photo contrast percentiles. Review product detail and the separate group-size finding.'
            check.measurement.update(foreground_fraction=fraction)
    return {'foreground_fraction':fraction,'foreground_reliable':seg.reliable}


def guard_background_focus(checks, products, coverage, scope):
    """A blank background is not a focus target; retain the raw full-frame observation."""
    photo=next(c for c in checks if c.identifier=='photo.blur')
    original=photo.measurement.pop('whole_photo_observation',None)
    if original:
        photo.status=Status(original['status']);photo.reason_code=original['reason_code']
        photo.explanation=original['explanation'];photo.threshold=original['threshold']
        photo.measurement.pop('assessment_scope',None);photo.measurement.pop('evidence_finding_ids',None)
    included=[p for p in products if p.get('intended',True)]
    local=[c for p in included for c in checks if c.identifier in p['finding_ids'] and c.identifier.endswith('.blur')]
    contrast=next(c for c in checks if c.identifier=='photo.contrast').measurement.get('score')
    if (photo.reason_code=='insufficient_signal' and coverage.get('method')=='general_foreground' and
        coverage.get('intended_complete') and scope['foreground_reliable'] and
        contrast is not None and contrast<15 and
        0<scope['foreground_fraction']<=.05 and local and len(local)==len(included) and
        all(c.status==Status.PASS for c in local)):
        photo.measurement.update(whole_photo_observation={'status':str(photo.status),'reason_code':photo.reason_code,
            'explanation':photo.explanation,'threshold':copy.deepcopy(photo.threshold)},
            assessment_scope='included_products',evidence_finding_ids=[c.identifier for c in local])
        photo.status=Status.PASS;photo.reason_code=None
        photo.explanation='Blank background has too little detail for a full-frame focus decision. Every included product passes its own DIP focus check; the original full-frame measurement is retained.'
        photo.threshold={**photo.threshold,'scope_policy':'blank-background-subject-focus-v1',
            'calibration':'Declared scope rule using existing product checks; no new full-frame sharpness cutoff.'}


def evidence_png(array):
    """Use lossless, faster PNG compression without changing the frozen legacy encoder."""
    output=BytesIO()
    Image.fromarray(array.astype(np.uint8)).save(output,format='PNG',compress_level=3)
    return output.getvalue()


def _draw(rgb, masks, products):
    union=np.zeros(rgb.shape[:2],bool)
    overlay=rgb.copy()
    colors=[(25,173,156),(94,106,229),(229,144,47),(196,78,139)]
    for i,mask in enumerate(masks):
        union|=mask
        overlay[mask]=(.7*overlay[mask]+.3*np.array(colors[i%len(colors)])).astype(np.uint8)
    return evidence_png(union*255),evidence_png(overlay)


def encode_evidence(rgb, masks, input_hash):
    """Reuse immutable PNGs only for identical input pixels, ordered masks, and encoder."""
    digest=hashlib.sha256()
    for mask in masks:digest.update(np.ascontiguousarray(mask))
    key=(input_hash,rgb.shape,len(masks),digest.digest(),evidence_png,_draw)
    with EVIDENCE_LOCK:
        cached=EVIDENCE_CACHE.get(key)
        if cached is not None:
            EVIDENCE_CACHE.move_to_end(key)
            return (*cached,True)
    mask_png,overlay_png=_draw(rgb,masks,[])
    value=(evidence_png(rgb),mask_png,overlay_png)
    size=sum(map(len,value))
    if size<=LOCALIZATION_CACHE_BYTES:
        with EVIDENCE_LOCK:
            EVIDENCE_CACHE[key]=value
            while len(EVIDENCE_CACHE)>4 or sum(sum(map(len,v)) for v in EVIDENCE_CACHE.values())>LOCALIZATION_CACHE_BYTES:
                EVIDENCE_CACHE.popitem(last=False)
    return (*value,False)


def inspect_auto(data, *, calibration=None):
    started=time.perf_counter()
    input_hash=hashlib.sha256(data).hexdigest()
    rgb,notes=decode_image(data)
    p=policy() if calibration is None else calibration
    p={**p,'score_verdict':score_policy(p.get('score_verdict',DEFAULT_VERDICT_POLICY))}
    checks,whole=photo_findings(rgb,p)
    localization_started=time.perf_counter()
    masks,coverage,seg,cache_hit=localize(rgb,input_hash)
    localization_ms=(time.perf_counter()-localization_started)*1000
    products=[]
    for number,mask in enumerate(masks,1):
        local,item=product_findings(rgb,mask,number,p)
        descriptions=coverage.get('region_descriptions',[])
        if number<=len(descriptions):item.update(descriptions[number-1])
        products.append(item);checks.extend(local)
    selection=select_intended(products,checks,rgb.shape[1],rgb.shape[0])
    coverage,seg=coverage_evidence(rgb,masks,coverage,products,segmentation=seg)
    contrast_scope=guard_photo_contrast(checks,rgb,masks,seg)
    guard_background_focus(checks,products,coverage,contrast_scope)
    checks.extend(coverage_findings(products,coverage))
    checks.extend(group_findings(products,rgb.shape[1],rgb.shape[0],p,accepted=coverage['intended_complete']))
    encoding_started=time.perf_counter()
    original_png,mask_png,overlay_png,evidence_hit=encode_evidence(rgb,masks,input_hash)
    encoding_ms=(time.perf_counter()-encoding_started)*1000
    meta={'one_click':True,'coverage':coverage,'whole_photo':whole,
         'intent_selection':selection,
         'provenance':{'operation':'inspection','measurement_version':PIPELINE_VERSION,
             'profile_version':p.get('feature_version'),
             'profile_sha256':hashlib.sha256(json.dumps(p,sort_keys=True,allow_nan=False).encode()).hexdigest(),
             'correction_version':None,'scoring_version':PIPELINE_VERSION},
         'contrast_scope':contrast_scope,
         'timings':{'localization_ms':round(localization_ms,2),'localization_cache_hit':cache_hit,
                    'encoding_ms':round(encoding_ms,2),'evidence_cache_hit':evidence_hit,
                    'definition':'Processing includes decoding, DIP, localization, scoring and evidence encoding or lookup; excludes database/network time.'},
         'calibration_disclosure':p.get('disclosure','Calibration unavailable; measured evidence is retained.')}
    partition_regions(meta,products)
    result=InspectionResult(str(uuid4()),datetime.now(timezone.utc).isoformat(),input_hash,
        rgb.shape[1],rgb.shape[0],PIPELINE_VERSION,p,PhotoContext.UNCERTAIN,checks,notes,meta,
        0,original_png,mask_png,overlay_png)
    result.score()
    result.duration_ms=round((time.perf_counter()-started)*1000,2)
    result.segmentation['timings']['processing_ms']=result.duration_ms
    return result


def amend_inspection(record, *, points=None, confirm_coverage=False, intended_numbers=None):
    started=time.perf_counter()
    saved=json.loads(record['result_json'])
    if not saved['segmentation'].get('one_click'):raise ValueError('Open a current one-click inspection first.')
    args={f.name:saved[f.name] for f in fields(InspectionResult) if f.name in saved}
    derived={'photo.coverage','photo.group_size','photo.group_placement','photo.group_margins'}
    args.update(checks=[CheckResult(**{**c,'status':Status(c['status'])}) for c in saved['checks'] if c['identifier'] not in derived],
        original_png=record['original_png'],mask_png=record['mask_png'],overlay_png=record['overlay_png'])
    result=InspectionResult(**args)
    result.inspection_id=str(uuid4());result.created_at=datetime.now(timezone.utc).isoformat()
    result.segmentation=json.loads(json.dumps(saved['segmentation']))
    provenance=result.segmentation.get('provenance',{})
    measurement_version=provenance.get('measurement_version',
        None if result.segmentation.get('source_inspection_id') else saved['pipeline_version'])
    result.segmentation['provenance']={**provenance,'operation':'correction',
        'measurement_version':measurement_version,
        'measurement_source_inspection_id':provenance.get('measurement_source_inspection_id',saved['inspection_id']),
        'profile_version':provenance.get('profile_version',result.profile.get('feature_version')),
        'profile_sha256':provenance.get('profile_sha256',hashlib.sha256(json.dumps(result.profile,sort_keys=True,allow_nan=False).encode()).hexdigest()),
        'correction_version':PIPELINE_VERSION,'scoring_version':PIPELINE_VERSION}
    products=detected_regions(result.segmentation)
    if intended_numbers is not None:
        numbers={p.get('region_id',p['number']) for p in products}
        if (not isinstance(intended_numbers,list) or not intended_numbers or
                any(type(n) is not int or n not in numbers for n in intended_numbers) or
                len(set(intended_numbers))!=len(intended_numbers)):
            raise ValueError('Select at least one existing numbered product; numbers must be unique integers.')
        for product in products:
            product.update(intended=product.get('region_id',product['number']) in intended_numbers,intent_source='user',intent_ambiguous=False,
                intent_reason='User selected intended products; mask accuracy remains unverified.')
    rgb=np.asarray(Image.open(BytesIO(record['original_png'])).convert('RGB'))
    if points is not None:
        mask=polygon_mask(points,(rgb.shape[1],rgb.shape[0]))
        number=max((p.get('region_id',p['number']) for p in products),default=0)+1
        local,item=product_findings(rgb,mask,number,result.profile)
        item['source']='user-marked region; not a verified reference'
        result.segmentation['provenance'].setdefault('added_region_measurements',[]).append({
            'region_id':number,'measurement_version':PIPELINE_VERSION,
            'profile_sha256':result.segmentation['provenance']['profile_sha256']})
        result.checks.extend(local);products.append(item)
        old=np.asarray(Image.open(BytesIO(record['mask_png'])))>0
        result.mask_png=evidence_png((old|mask)*255)
        overlay=rgb.copy();overlay[old|mask]=(.7*overlay[old|mask]+.3*np.array([25,173,156])).astype(np.uint8)
        result.overlay_png=evidence_png(overlay)
    included=[p for p in products if p.get('intended',True)]
    if confirm_coverage and not included:raise ValueError('Mark at least one product before confirming coverage.')
    applicability={identity:p.get('intended',True) for p in products for identity in p['finding_ids']}
    for check in result.checks:
        if check.identifier in applicability:check.required=applicability[check.identifier]
    coverage=result.segmentation['coverage']
    complete=bool(confirm_coverage)
    coverage.setdefault('foreground_complete',coverage['coverage_complete'])
    coverage.setdefault('foreground_reason',coverage['reason'])
    coverage.update(coverage_complete=complete,intended_complete=complete,user_confirmed=complete,mask_verified=False,
        acceptance_source='user' if complete else 'unresolved',reason_code=None if complete else 'intended_coverage_unconfirmed',
        reason='User confirms every intended product is numbered. This does not verify mask accuracy.' if complete else
            'A region was added; select the intended items and confirm whether every intended product is numbered.')
    scope=dict(result.segmentation.get('contrast_scope',{'foreground_fraction':0,'foreground_reliable':False}))
    if points is not None:scope['foreground_reliable']=False
    guard_background_focus(result.checks,products,coverage,scope)
    result.segmentation['source_inspection_id']=saved['inspection_id']
    result.checks.append(coverage_findings(products,coverage)[1])
    result.checks.extend(group_findings(products,result.width,result.height,result.profile,accepted=complete))
    partition_regions(result.segmentation,products)
    result.score()
    result.duration_ms=round((time.perf_counter()-started)*1000,2)
    result.segmentation['timings']={'processing_ms':result.duration_ms,'localization_reused':'saved masks; no detection rerun',
        'definition':'Child processing includes recalculation and any new evidence encoding; excludes database/network time.'}
    return result
