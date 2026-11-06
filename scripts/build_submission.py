"""Assemble local report, diagram, editable native-chart presentation and demo."""
import html
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE


def table(headers, rows):
    return '<table><tr>'+''.join('<th>'+html.escape(str(v))+'</th>' for v in headers)+'</tr>'+''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</table>'


def main():
    out=Path('submission');fig=out/'figures';fig.mkdir(exist_ok=True)
    if (out/'report.html').exists():raise ValueError('Preserve assembled outputs.')
    final=json.loads(Path('reports/revision-phase6/comparison.json').read_text())
    dev=json.loads(Path('reports/revision-phase4/comparison.json').read_text())
    cal=json.loads(Path('data/revision-calibrated-profile.calibration.json').read_text())
    browser=json.loads(Path('reports/revision-phase7/verified-run/verification.json').read_text())
    names=list(final['methods'])
    canvas=Image.new('RGB',(1600,650),'white');draw=ImageDraw.Draw(canvas)
    font=ImageFont.truetype(r'C:\Windows\Fonts\arial.ttf',25)
    boxes=[(20,70,'Image input\nSafe decode / orient'),(430,70,'LAB threshold\nClosing / components'),(840,70,'GrabCut / region checks\nErosion / dilation'),(1250,70,'Quality + layout\nMeasurements'),(840,430,'Rules + uncertainty\nPass / Fail / Review'),(1250,430,'Evidence + SQLite\nSaved photos')]
    for x,y,label in boxes:
        draw.rounded_rectangle((x,y,x+320,y+140),radius=15,fill='#e7f5f5',outline='#087e8b',width=4)
        draw.multiline_text((x+20,y+35),label,font=font,fill='#173044',spacing=10)
    for x in [340,750,1160]:
        draw.line((x,140,x+80,140),fill='#173044',width=4);draw.polygon([(x+80,140),(x+64,132),(x+64,148)],fill='#173044')
    draw.line((1410,210,1410,340,1000,340,1000,430),fill='#173044',width=4)
    draw.polygon([(1000,430),(992,414),(1008,414)],fill='#173044')
    draw.line((1160,500,1250,500),fill='#173044',width=4);draw.polygon([(1250,500),(1234,492),(1234,508)],fill='#173044')
    canvas.save(fig/'architecture.png')
    sections=[]
    def add(title,body):sections.append((title,body))
    add('Abstract','''<p>Framecheck inspects one product photograph using classical Digital Image Processing. Six quality and six background/layout checks produce explained Pass, Fail or Manual Review decisions, visual masks and bounding boxes, and persistent SQLite history.</p><p>The frozen fresh test has 337 records from 60 new source groups. The default demo obtains Macro-F1 0.5043, recall 50.49%, false rejects 14.29% (1/7), and review 30.27%. New calibrated cutoffs reject all seven acceptable test groups. Neither calibration nor the rejected edge-confidence hypothesis is promoted. Course deliverables are complete; autonomous marketplace accuracy is not established.</p>''')
    add('Problem, scope and course requirements','''<p>HSR-AIML-056: Automated E-commerce Image Quality and Compliance Inspector. The goal is consistent, explainable first inspection of product photos with explicit quality and presentation rules. The company in the topic bank is an industry reference, not a sponsor.</p><p>The inspected DIP course guidelines require meaningful image processing, intermediate figures, baseline/alternative comparison, quantitative evaluation, failures, reproducible code, report and demonstration. Learnt models and cloud architecture are optional. Student/team names and roll numbers were not supplied and must be entered by the submitter.</p><p>Scope excludes semantic recognition, automatic person detection, image correction, additional datasets, batch/cloud deployment and authentication. The frontend stays simple.</p><img class="wide" src="figures/architecture.png" alt="DIP architecture">''')
    add('DIP implementation',table(['Operation','Implementation / contribution'],[
        ['Preprocessing','Safe JPG/PNG decode; 10 MB and 24 MP limits; EXIF orientation; RGB conversion; transparent pixels composite on white. Analysis long side capped at 512; native resolution preserved.'],
        ['Segmentation','LAB border-median color distance, threshold mask, 3x3 closing, connected-component filtering, seeded GrabCut, stability/refinement/border/size checks.'],
        ['Morphology','Closing repairs gaps; erosion selects product interiors; dilation excludes uncertain boundary pixels from background sampling. Preserve holes, disconnected parts and edge contact.'],
        ['Blur','Laplacian variance in product interior. Noise and weak contrast can invalidate interpretation.'],
        ['Low contrast','Grayscale P95-P5 inside product.'],
        ['Noise','Robust MAD of high-pass residuals in sufficiently supported low-gradient patches. Texture can confound.'],
        ['Exposure','Dark/bright fractions and median luminance inside product; natural color can resemble exposure defects.'],
        ['Resolution','Native short/long sides at least 60/80 pixels.'],
        ['Background color','At least 90% of assessed background pixels: every RGB channel >=235 and channel spread <=15.'],
        ['Background uniformity','P90 LAB DeltaE76 distance from median <=10. Visible pattern alone does not prove numerical violation.'],
        ['Object/layout','Dominant normalized bounding-box extent 55-90% (not area), center offset <=10%, every margin >=2.5%; geometry within one native pixel requires review.'],
        ['Possible cropping','Frame contact requests review; it never proves missing content or produces a cropping Fail by itself.'],
        ['Decision','All applicable checks run; overall precedence Fail, then Review, then Pass. Native resolution stays independent.']]))
    add('Explainability, profile integrity and persistence','''<p>Product-dependent automatic checks require explicit isolated-product context and supported regions. Model-worn, unsure, unsupported-size, insufficient-sample and boundary cases retain review. New result JSON includes reason codes, measurements, thresholds, context, effective profile and pipeline version.</p><p>Profiles validate schema, hash, compatible pipeline/core code, size support, development ID and explicit target outcome. Missing/stale rules are clearly unavailable. Failed-validation profiles remain experimental. Browser context choice is required; errors retain profile/context/numbers.</p><p>SQLite stores one inspection and twelve check rows. Prepared original, binary mask and highlighted overlay are native PNG BLOBs. Detailed JSON is TEXT and excludes image bytes. Original upload bytes are not preserved exactly; their SHA-256 is retained. Saved pages/report/evidence are read from SQLite rather than recomputed. Existing history remains compatible.</p>''')
    add('Dataset, annotation and split verification',table(['Split','Original groups','Records'],[['Train',180,460],['Validation',60,340],['Fresh test',60,337],['Total',300,1137]])+'''<p>Only Fashion Product Images Small is used: apparel, footwear, bags and watches, 75 originals per category overall, native 60x80. Source: <a href="https://www.kaggle.com/datasets/paramaggarwal/fashion-product-images-small">paramaggarwal/Fashion Product Images Small</a>. The previously inspected test set is historical diagnostic evidence.</p><p>Fresh selection used seed 42 and 15 groups per category before detector predictions. Prior identities, encoded/decoded duplicates and conservative display-name matches were excluded. Twelve perceptual candidates were visually reviewed. No fresh/old ID, source/hash or encoded/decoded image overlap was found; semantic grouping is not exhaustive.</p><p>All annotations are AI-only, not human-verified. Unknowns remain unknown: 1,252 test labels. Twenty source-coordinate polygon masks, five per category, and 175 reviewed transformed masks yield 195 fresh-test references, approximately 1-2 pixels uncertain. Variants share groups and are correlated.</p><p>Fresh records: 60 originals, 240 single-transform, 27 mixed and ten unchanged controls. Mixed blur/noise has seven eligible groups, three below target. All twelve defect categories still have at least ten positive test groups. Only seven fully labelled acceptable test groups exist.</p><p>All 277 new variants replay exactly and crops retain at least 75% of product pixels. Two background labels were corrected in explicit versions under the fixed numerical rule. Authoritative data: data/revision-v2/benchmark-v2.2.jsonl. Historical sources, annotations and user history were preserved.</p>''')
    add('Development comparison and safe calibration',table(['Split','Raw mask IoU','Closing','Components','Refined'],[[s]+[f'{v[k]:.4f}' for k in ['raw','closed','components','refined']] for s,v in dev['group_mean_mask_iou'].items()])+'''<p>IoU averages each source group first. Closing improves this measure; refinement helps training more than closing alone but does not improve the corresponding validation mean. AI references and correlated variants limit interpretation.</p><p>Tenengrad blur-ranking AUC is 0.5522/0.4671 on train/validation; Laplacian is 0.7967/0.7006. Tenengrad is not substituted. Segmentation changes can introduce boundary energy and inflate sharpness.</p><p>A fixed Sobel-boundary/stability confidence hypothesis reduces validation reviews from 113 to 108 but admits five poor masks (IoU 0.0419-0.2732). Its minimum newly-confident IoU gate of 0.70 fails. It stays offline.</p><p>Calibration uses 460 training and 340 validation records, 315 eligible training measurements, two passes and 84 logged candidate evaluations. Joint feasibility (false rejects <=10%, reviews <=30%) precedes F1, recall and lower rejection. No visited candidate is feasible. Deterministic replay matches, with zero test reads. The best-F1 infeasible profile is frozen as experimental; the app default stays Demo White Background.</p><p>Four methods, all inspection code hashes, the manifest/development digest and bootstrap policy were frozen before fresh evaluation. Historical cutoffs are a diagnostic comparator, not a compatible deployment profile. No neural network was trained.</p>''')
    stage=dev['examples'][0]['file'];bad=dev['examples'][-2]['file']
    add('Intermediate DIP evidence',f'''<p>Fourteen stage worksheets show input, LAB distance, raw threshold mask, closing, components, refined mask, eroded interior, excluded-edge background sampling, Sobel edges and overlay.</p><figure><img class="wide" src="../reports/revision-phase4/{stage}"><figcaption>Development source {dev['examples'][0]['id']}.</figcaption></figure><figure><img class="wide" src="../reports/revision-phase4/{bad}"><figcaption>Rejected checker-background confidence: strong stable edges can belong to the background.</figcaption></figure>''')
    rows=[]
    for name,m in final['methods'].items():
        v=m['metrics'];rows.append([name,f"{v['macro_f1']:.4f}",f"{100*v['defect_recall']:.2f}% ({v['true_detected_defects']}/{v['actual_defect_instances']})",f"{100*v['false_reject_rate']:.2f}% ({v['false_rejects']}/{v['acceptable_images']})",f"{100*v['manual_review_rate']:.2f}% ({v['manual_reviews']}/337)",f"{m['latency_ms']['p95']:.2f} ms"])
    add('Frozen fresh-test results',table(['Method','Macro-F1','Defect recall','False rejects','Review','CPU p95'],rows)+'''<p>Primary positives are definitive Fail checks. Unresolved true defects count as misses. Unknown truth is excluded only from its category denominator. Macro-F1 averages all twelve categories; recall counts defect instances. False rejects use fully labelled acceptable records only.</p><p>All four methods have the same masks: mean IoU 0.8802 on 195 AI references from 20 groups. The edge alternative changes eligibility, not segmentation masks. Full per-category, original/synthetic/mixed/control, severity/category, review-reason and automatic-only secondary slices are in the JSON report.</p><p>Latency includes decode, measurements, classification and three native PNG encodings; excludes SQLite, UI and report writes. Hardware/load variation limits speed comparisons. Every method stays below two seconds p95 on these dataset-sized images.</p>''')
    ci=final['source_group_bootstrap']['methods']['demo']
    add('Uncertainty and target outcomes',table(['Demo metric','95% lower','95% upper','Defined draws'],[[k,f"{v['lower']:.4f}",f"{v['upper']:.4f}",v['defined_replicates']] for k,v in ci.items()])+'''<p>Intervals use 1,000 paired source-group resamples, seed 42, retaining all variants together. Paired method deltas are also reported. These are conditional empirical percentile intervals, not universal guarantees. Seven acceptable groups provide weak false-reject evidence; extreme observations can produce degenerate bootstrap intervals.</p><p>The demo misses F1 >=0.75, recall >=0.80, false rejects <=10%, and reviews <=30%. New calibration raises F1 but rejects 7/7 acceptable groups. The rejected edge method cannot be approved by better headline metrics after failing mask safety. No method is certified for autonomous marketplace rejection.</p>''')
    baseline=final['methods']['demo']
    add('Per-defect baseline metrics',table(['Defect','Precision','Recall','F1','Positive instances','Groups','Unknown'],[[d]+[f'{v[k]:.3f}' for k in ['precision','recall','f1']]+[v['positive_support'],v['positive_product_groups'],v['unknown_labels']] for d,v in baseline['metrics']['per_category'].items()])+'<p>Cropping primary F1/recall remain zero. True-case referrals and referral recall are separate advisory outcomes, not definitive detections.</p>')
    cases=''
    for reason in ['Correct known-label case','False reject','Missed or unresolved defect','Manual Review']:
        e=next((e for e in baseline['examples'] if e['reason']==reason),None)
        if e:cases+=f'<figure><img class="example" src="../reports/revision-phase6/{e["original"]}"><img class="example" src="../reports/revision-phase6/{e["overlay"]}"><img class="example" src="../reports/revision-phase6/{e["mask"]}"><figcaption>{html.escape(reason+": "+e["id"])}</figcaption><p>{html.escape("; ".join(e["explanations"][:2]))}</p></figure>'
    add('Success and failure analysis',cases+'''<p>Patterned backgrounds produce stable wrong masks. Dark/light product color can resemble exposure defects; texture/noise contaminate sharpness. One pixel matters at low resolution. Model-worn and unsupported contexts legitimately abstain.</p><p>Calibration alone did not meet accuracy targets. This does not prove a neural network is necessary: annotation quality, source scope, measurement confounding and segmentation remain limiting. Training was not justified within the fixed course scope. No method was retuned after this fresh test. A future revision needs a new untouched evaluation set.</p>''')
    add('Software, browser and persistence verification',f'''<p>106 software tests pass. Real {html.escape(browser['browser']['product'])} desktop (1280x900) and mobile (390x844) checks cover native required inputs, keyboard focus/submission, fractional settings, readable errors with preserved selections, loaded evidence, history navigation and overflow.</p><p>Eight isolated saved inspections and 96 check rows verified all twelve checks, effective profile, evidence dimensions/hashes, duplicate POST handling, invalid input rejection, SQLite integrity, separate-process reopening and legacy rendering. Existing user data and frozen benchmark were unchanged.</p><figure><img class="wide" src="../reports/revision-phase7/verified-run/desktop-home.png"><figcaption>Actual desktop screenshot with keyboard focus.</figcaption></figure><figure><img class="phone" src="../reports/revision-phase7/verified-run/mobile-result.png"><figcaption>Actual mobile result and evidence.</figcaption></figure>''')
    add('Reproduction and conclusion','''<p>Run .&#92;.venv&#92;Scripts&#92;python.exe app.py --port 8505. Use submission/manual-testing.md and its sample folder. Frozen methods: reports/revision-phase5/methods.freeze.json. Full results: reports/revision-phase6/comparison.json and comparison.html. Intermediate evidence: reports/revision-phase4/comparison.html.</p><p>Source, dependency declarations, data manifest/freeze reports, exact commands, report, editable presentation and demo script accompany the submission. Preserve frozen artifacts; reproducibility runs require fresh outputs/workspaces.</p><p>The bounded DIP course project is complete with explicit failed accuracy targets. AI-only synthetic evidence does not establish human-verified or real-marketplace accuracy. The application remains a demonstration and assisted-review tool.</p>''')
    css='body{font:15px/1.5 Arial,sans-serif;color:#173044;max-width:1050px;margin:30px auto;padding:20px}h1{font-size:30px}h2{font-size:22px;break-after:avoid}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:7px;border:1px solid #c5d4da;text-align:left}th{background:#e9f3f5}figure{margin:20px 0;break-inside:avoid}.wide{width:100%}.example{width:31%;height:180px;object-fit:contain}.phone{max-height:650px;max-width:100%}@page{size:A4;margin:15mm}@media print{body{font-size:10pt;margin:0;padding:0}table{font-size:8pt}.phone{max-height:220mm}}'
    body='<h1>Framecheck</h1><p><b>HSR-AIML-056: Automated E-commerce Image Quality and Compliance Inspector</b></p><p>DIP course project | Runtime 0.3.0 | AI-only demonstration</p><p>Student/team names and roll numbers: ____________________</p>'+''.join(f'<section><h2>{i}. {t}</h2>{b}</section>' for i,(t,b) in enumerate(sections,1))
    (out/'report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Framecheck course report</title><style>'+css+'</style>'+body+'</html>',encoding='utf-8')
    (out/'report.md').write_text('# Framecheck - HSR-AIML-056\n\nStudent/team details: enter before submission.\n\n'+''.join(f'## {i}. {t}\n\n{b}\n\n' for i,(t,b) in enumerate(sections,1)),encoding='utf-8')
    prs=Presentation();prs.slide_width=Inches(13.333);prs.slide_height=Inches(7.5)
    def slide(title,bullets,picture=None):
        s=prs.slides.add_slide(prs.slide_layouts[6]);s.background.fill.solid();s.background.fill.fore_color.rgb=RGBColor.from_string('F5F8FA')
        box=s.shapes.add_textbox(Inches(.55),Inches(.35),Inches(12.2),Inches(.8));p=box.text_frame.paragraphs[0];p.text=title;p.font.size=Pt(30);p.font.bold=True;p.font.color.rgb=RGBColor.from_string('173044')
        text=s.shapes.add_textbox(Inches(.65),Inches(1.4),Inches(12),Inches(5.5 if not picture else 1)).text_frame;text.word_wrap=True
        for i,line in enumerate(bullets):
            p=text.paragraphs[0] if i==0 else text.add_paragraph();p.text=line;p.font.size=Pt(22 if not picture else 18);p.space_after=Pt(18)
        if picture:
            im=Image.open(picture);w,h=im.size;scale=min(11.2/w,4.5/h)
            s.shapes.add_picture(str(picture),Inches((13.333-w*scale)/2),Inches(2.5),width=Inches(w*scale),height=Inches(h*scale))
        footer=s.shapes.add_textbox(Inches(.65),Inches(7.1),Inches(12),Inches(.25));footer.text=f'Framecheck | DIP course project | {len(prs.slides)}';footer.text_frame.paragraphs[0].font.size=Pt(11)
        return s
    slide('Framecheck: explainable product-image inspection',['HSR-AIML-056 | Classical Digital Image Processing','Quality, segmentation, morphology, background and layout analysis','Student/team details: enter before submission.'])
    slide('Problem and bounded scope',['One photograph; twelve independent checks.','Pass / Fail / Manual Review with measurements and visual evidence.','Simple local interface and SQLite history. No neural-network training or cloud platform.'])
    slide('Where DIP happens',['Pixel measurements and region analysis drive decisions.'],fig/'architecture.png')
    slide('Segmentation and morphology',['LAB threshold, closing/components, GrabCut, erosion and dilation.'],Path('reports/revision-phase4')/stage)
    slide('Data and honest annotation',['300 original groups: 180 training / 60 validation / 60 fresh test.','1,137 records; 337 fresh-test examples. No prior identity/hash overlap.','AI-only labels and approximate masks; unknown truth retained.','Only seven acceptable test groups. Variants share source groups.'])
    slide('Calibration and alternatives',['84 candidate evaluations; none meets joint rejection/review constraints.','Tenengrad ranks blur worse than Laplacian on development data.','Edge confidence admits wrong checker masks and fails safety.','Default stays demo; no unsuccessful method is promoted.'])
    s=slide('Frozen fresh-test comparison',['Baseline F1 0.504; recall 50.49%; false rejects 1/7; reviews 102/337.'])
    data=CategoryChartData();data.categories=['Demo','New calibration','Rejected edge','Historical']
    for key,label in [('macro_f1','Macro-F1'),('defect_recall','Recall'),('false_reject_rate','False rejects'),('manual_review_rate','Review')]:
        data.add_series(label,[final['methods'][n]['metrics'][key] for n in names])
    chart=s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,Inches(.8),Inches(2.5),Inches(11.7),Inches(4.2),data).chart
    chart.has_legend=True;chart.value_axis.minimum_scale=0;chart.value_axis.maximum_scale=1
    slide('A useful failure: stable edges can be background',['Mask safety overrides a superficially improved F1/review rate.'],Path('reports/revision-phase4')/bad)
    slide('Software and actual browser verification',['106 software tests; Chrome desktop/mobile, keyboard and fractional settings.','Eight saved inspections; 96 check rows; evidence and restart verified.','Existing history and source data preserved; old results still render.'])
    slide('Conclusion and live demonstration',['Course deliverables complete; accuracy targets were not achieved.','Do not use calibrated cutoffs for autonomous rejection.','Demo: upload three examples, inspect a mask, reopen Saved photos.','Future method changes need a new untouched evaluation set.'])
    assert len(prs.slides)==10
    prs.save(out/'presentation.pptx')
    (out/'demo-script.md').write_text('''# Six-minute demonstration
1. Problem and honest scope (30 s): twelve checks, AI-only labels, failed accuracy targets.
2. DIP pipeline (60 s): RGB/LAB, filtering, thresholding, morphology, GrabCut and region measurements. Show one stage sheet.
3. Data and comparison (60 s): group-safe fresh split, unknown truth, seven acceptable groups, paired group-bootstrap intervals.
4. Negative result (45 s): checker mask failure; fewer reviews do not justify unsafe confidence. Explain rejected Tenengrad/training choices.
5. Live app (90 s): open port 8505, upload default-profile Pass/Fail/Review examples, explicitly choose Only the product, expand an explanation/mask, reopen Saved photos.
6. Persistence and limitations (45 s): saved JSON/profile and verified restart; conclude without claiming marketplace approval.

Enter actual student/team names, roll numbers and truthful contributions before submission. These details were not supplied.
''',encoding='utf-8')
    assert (out/'report.html').stat().st_size>10000 and (out/'presentation.pptx').stat().st_size>10000
    print('Built report HTML/Markdown, architecture figure, ten-slide editable PPTX with native metric chart, and demo script.')


if __name__=='__main__':main()

