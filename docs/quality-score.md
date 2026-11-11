# Whole-photo DIP quality score

New one-click reports use `1.7.0-scoped-assessment-dip`. The backend saves a top-level
`quality_score` object with the score, verdict, assessed coverage, possible range,
component evidence, main deductions, and method version. SQLite needs no schema
change. Old reports remain unchanged and display “Not scored” when a score is absent; reinspection
creates a new report. User overrides create a child report and recalculate its score.

`segmentation.report_schema_version = 2` separates intended `products` from
`incidental_regions`. Products have consecutive display numbers starting at one;
incidental regions have `number: null`. Both retain an immutable `region_id` for
finding references and user overrides. A display Product 2 can refer to detection
region 3 (`product-3.blur`); score evidence uses region IDs, never display numbers.
Explicit `product_count`, `incidental_count`, and `detection_count` prevent treating
all detections as products. Historical mixed lists remain readable without mutation.
The results page offers reinspection as a new report when old rules are present.

Reliable supported detection can accept estimated intended coverage automatically and
enable group framing without user confirmation. Acceptance requires a stable DIP
background, complete detector accounting, no substantial missing-region evidence,
and no ambiguous intended candidate. Weak-tone regions reuse the detector floor
of `max(24, round(analysis_pixels * .002))`; raw residuals retain the existing
24-pixel warning floor. Weak-tone evidence gates plain-background
foreground detection; supported person scenes use detector accounting and raw residuals,
because weak-tone background gradients do not establish missing apparel. Small residuals
remain advisory. Coverage records `acceptance_source` separately from `user_confirmed`;
automatic acceptance never verifies a mask or clears an independent required finding.
Missing products, unreliable detection, substantial missing regions, and ambiguous
selection retain specific coverage reason codes. Explicit corrections still create
immutable children. Numerical quality thresholds and score weights are unchanged.

## Separation and item-count evidence

The detector proposes separate regions when the original color mask contains
substantial disjoint pieces joined by cleanup. Each piece must cover at least 20%
of the retained region, their bounding boxes must separate on an axis, and together
they must explain at least 90% of it. Proposals remain unverified. Narrow joins
between substantial eroded cores and broad nearly solid horizontal silhouettes also
flag possible touching items. The silhouette trigger uses width/height at least 2
and mask/bounding-box area at least 95%. These are declared suspicion rules, not
validated instance accuracy. The same checks apply within supported apparel labels.

`item_count_uncertain` and `separation_evidence` disclose the ambiguity. Required
coverage receives `item_count_unresolved`, leaving photo/product checks available.
Users can mark separate items, exclude a combined candidate, and save intended items
as an immutable child. Confirmation is an intent assertion, never mask verification.
Clear separated products and the existing sneaker retain automatic acceptance.
One long box/panel can also trigger the broad-silhouette warning. Labelled instance
and intent data are still needed to distinguish these cases; hidden or identical
touching products cannot be guaranteed detected.

## Focus on mostly blank backgrounds

The `blank-background-subject-focus-v1` scope rule applies only when reliable plain
foreground occupies at most the existing 5% limit, intended scope is resolved,
whole-photo contrast is below the existing 15-point signal floor, and photo focus
is unavailable for signal reasons. Every included product must independently Pass
its existing DIP focus check. The photo-focus finding then records a subject-focus
assessment, its supporting product finding IDs, and the full original observation
(status, reason, explanation, threshold, and raw score). Whole-photo diagnostics,
contrast uncertainty, noise, exposure, and resolution stay independent.

This changes the declared assessment scope, not a numerical quality cutoff or an
empirical calibration claim. Calibration-boundary and unsupported-Fail reviews are
never cleared this way. Uncertain product focus or unresolved item count prevents
substitution. Selection changes re-evaluate the support; adding a region restores
the original full-frame observation pending supported scope. Human-labelled natural
blur, uniform materials, and varied product scales still need validation.

## Declared weights

These are explicit product-policy choices, not fitted or independently validated
quality ratings. The weights total 100. They summarize the current DIP findings,
including their existing calibration limits; they do not establish marketplace approval.

| Scope | Finding | Points |
|---|---|---:|
| Photo | Resolution | 8 |
| Photo | Sharpness / blur | 14 |
| Photo | Contrast | 8 |
| Photo | Noise | 10 |
| Photo | Shadow detail | 5 |
| Photo | Highlight detail | 5 |
| Intended products | Detail / pixel support | 4 |
| Intended products | Sharpness / blur | 8 |
| Intended products | Noise | 4 |
| Intended products | Contrast signal | 2 |
| Intended products | Clipping warning | 4 |
| Intended products | Mask-completeness warning | 3 |
| Intended products | Edge / possible cropping | 5 |
| Intended group | Size | 4 |
| Intended group | Placement | 3 |
| Intended group | Margins | 3 |
| Photo | Intended-product coverage | 10 |

Each product component uses the worst intended product finding: Fail takes
precedence, then uncertainty, then Pass. The component budget is counted once,
regardless of product count. Adding passing products cannot dilute an existing
failure. An excluded detection keeps its measurements and findings as context but
does not decide the verdict or product score. Whole-photo findings remain active.

- Pass earns the component's points.
- Fail earns zero and appears as a confirmed deduction.
- Manual Review, Disabled, or a missing expected finding earns zero and appears as
  withheld uncertainty. If no intended product is available, all 30 product points
  remain withheld. Weights are never redistributed over available checks.
- `score = 100 - confirmed_deductions - uncertainty_withheld`.
- `assessed_coverage_percent = 100 - uncertainty_withheld`: decisive **check weight**,
  not image area, product detection recall, or verified mask accuracy.
- The possible range is `[score, score + uncertainty_withheld]`. It shows the effect
  of unresolved checks; it is not a statistical confidence interval.
- A provisional score policy determines Pass, Needs Improvement, or Needs Review.
  Individual findings retain their original Pass, Fail, Manual Review, or Disabled
  states. No incomplete check earns credit or counts as a confirmed deduction.

## Provisional score-based verdict

Configure `score_verdict.pass_threshold` in `data/one-click-policy.json`; the initial
value is **75/100**. This is a declared provisional approval rule, not an accuracy
claim or a calibrated marketplace threshold. Existing DIP metric thresholds and
100-point weights are unchanged. Unknown policy fields, nonnumeric values, booleans,
nonfinite numbers, and values outside 0–100 produce **Needs Review**.

Decision order:

1. Invalid policy: **Needs Review**.
2. Confirmed required critical defect: **Needs Improvement**, regardless of score
   or other incomplete evidence.
3. Incomplete essential evidence: **Needs Review**, even above 75. Essential evidence
   means every required finding and every expected score component. Missing, disabled,
   excluded expected findings, or uncertainty on any included product block Pass.
   If one product fails a component and another is unassessed, its existing points
   remain a confirmed deduction, but the second product still needs review.
4. Complete score below the configured threshold: **Needs Improvement**.
5. Complete score at or above the threshold: **Pass**. Noncritical findings can still
   lose points and produce suggestions on a passing photo.

Critical checks are original photo resolution, photo/product blur and noise, severe
photo shadow/highlight loss, product edge/cropping, mask completeness, and intended
coverage. Only a confirmed Fail on an applicable required finding triggers the
critical gate; an uncertain observation remains incomplete evidence. Blur's existing
unvalidated-Fail guards remain active. These critical classifications are declared
policy, not measured natural-defect severity. Incidental products and advisory
foreground accounting do not trigger the gate. Unsupported required failures request
Review rather than silently allowing Pass.

Confirmed **severe framing** also blocks Pass, without changing deductions. Severity
is a declared policy: at least two native group-framing rules Fail, or group extent
falls below half the configured minimum. Findings carry `severe_framing` and the
rule. Mild isolated failures remain deductions. Boundary uncertainty remains Review;
unsupported or unresolved group measurements cannot create a severity decision.
This prevents the reproduced three-failure corner composition from Passing at 90.

The backend stores the policy version, effective threshold, critical findings,
incomplete finding IDs, and decision reason with `quality_score`. Top-level `status`,
the database history status, and `quality_score.verdict` use the same policy verdict.
`quality_score.check_verdict` preserves the earlier per-check aggregate; quality and
compliance subgroup statuses also remain per-check aggregates. Saved reports are not
recomputed on read; reinspection or correction creates a new child. Passing photos
retain evidence-based fixes for noncritical problems and simple photo-preservation
advice when all checks pass. Groq may explain only backend-approved findings and the
exact backend score summary; it cannot adjust scores, gates, or verdicts.

## Measurement and correction provenance

Version 1.9 stores `segmentation.provenance` with measurement, profile feature,
profile SHA-256, correction, and scoring versions. A selection correction keeps
the saved `pipeline_version`, profile, and inherited quality findings. Its current
correction/scoring implementation is recorded separately. Added marked regions
record their own current measurement version against the retained profile; they
do not relabel inherited photo or product measurements. Chained corrections keep
the original measurement source. No historical report is rewritten.

Fresh reinspection decodes the saved original and runs localization, DIP, and the
current profile again. Its UI says **Reprocessed original**. Corrections instead
say **Corrected selection — Saved measurements retained**. Older derived reports
that never recorded measurement origin disclose that gap rather than infer a
current measurement version. Their origins require fresh reinspection to establish.

Product cards expose all required blocking findings, including contrast, clipping,
mask warnings, disabled checks, and missing findings. Confirmed problems and
incomplete assessment get separate labels, advice groups, and point summaries.
The result now leads with a friendly readiness headline, saved score and verdict.
Cards use plain descriptions of the same underlying findings. Measurements, profile
versions, provenance and scoring mechanics stay in Details. Groq still cannot
introduce claims, alter the verdict, or change point accounting.

## Localization reuse and timing

Each cache miss creates one analysis segmentation and reuses it for detection and
coverage accounting. A process-local cache holds at most four entries and 48 MiB of
measured mask/analysis arrays. It keys uploaded bytes, native shape, pipeline/detector
identity, and local checkpoint file signatures. Arrays/metadata are copied to protect
previous results. Unresolved/model-unavailable localization is retried. A changed
score configuration always recomputes DIP findings and verdicts; cached localization
cannot reuse an old approval. Checkpoint replacement still requires restarting the
existing local model loader. No cache persists across app restarts.

Native garment labels now stream one bilinearly interpolated class at a time at
the original photo dimensions. Running maxima preserve the full native argmax,
first-index ties, and NaN behavior; hard labels are never resized instead of logits.
At sizes no larger than source logits, the existing full interpolation path avoids
streaming overhead. This reduces native-logit working memory without changing
mask coordinates or DIP thresholds. Optional dependency imports remain lazy.

New one-click evidence uses lossless PNG compression level 3, leaving the frozen
legacy encoder and calibrated-profile integrity hashes untouched. A separate
process-local cache holds at most four entries/48 MiB of immutable original/mask/
overlay PNGs. Keys bind input bytes, native dimensions, ordered mask contents,
drawing code, and encoder identity. Every inspection still recomputes its checks
and score. Cache lookup/encoding time and `evidence_cache_hit` are reported; saved
corrections reuse existing PNGs unless a region was added. Simultaneous misses can
still encode independently. Faster compression can increase stored PNG sizes.

`duration_ms` and Details `timings.processing_ms` include decoding, localization, DIP,
scoring, and all three evidence encodings. Localization and encoding durations and
cache-hit state are recorded separately. Child corrections disclose reuse of saved
masks and measure their own work. Upload/correction redirects expose `Server-Timing`:
`request` covers application time through committed storage and redirect preparation;
`processing` and `storage` identify their measured portions. These exclude subsequent
network transfer/browser rendering and optional explanation generation.

A lower score can mean uncertainty rather than confirmed poor photography. The UI
and Groq explanation distinguish those cases. A passing warning check means no
warning was observed; uncalibrated local contrast/exposure quality and unverified
masks remain disclosed limitations even when their warning checks pass.

## Automatic intended products

Selection runs on every detection after measurement. It uses visible mask area,
normalized framing, edge contact, and mask-completeness warnings across categories.
It contains no rule excluding trousers or preferring T-shirts.

An apparently complete anchor must occupy at least 3% of the image, have no edge
contact or mask warning, and have a maximum normalized center offset of at most 35%.
A small peripheral candidate is incidental when **all** conditions hold:

1. It contacts or approaches within 0.5% of a photo edge.
2. Its area is at most 55% of the largest complete anchor's area.
3. Its area is less than 60% of the largest detected region's area.
4. Its shorter normalized bounding-box extent is at most 30% of the frame.
5. Its maximum normalized center offset is at least 20%.

Version 1.8 also recognizes a smaller partial edge region aligned with a complete
anchor. Its area must be at most 65% of that anchor and less than 65% of the largest
region, its shorter normalized extent at most 40%, and its center offset at least
20%. Across the attachment, at least 90% of its width overlaps the anchor. Neither
normalized extent may exceed the anchor's. Along the direction toward the photo
edge, the gap is at most 2.5% of the frame, or the overlap at most 5% of the smaller
extent. This rule works on all four edges and does not use category labels.
`intent_evidence.continuation_anchor` records the supporting region identity.
These are declared geometric heuristics, not independently validated identities.

Dominant cropped products, lone cropped products, and ambiguous candidates stay
intended and retain their cropping finding. Complete full-length products remain
eligible. The saved `intent_evidence`, `intent_reason`, `intent_source`, and
`intent_ambiguous` fields explain each selection. Excluded regions retain numbering,
local findings, and original evidence. Only intended products receive numbered markers
on the original photo. Incidental regions appear as unnumbered previews under Details.

The optional override expands the existing intended-item and marking controls.
An always-visible **Change products (optional)** link beside the verdict
opens those controls, including the excluded regions. It does not require
confirmation before accepting an otherwise supported automatic selection.
Automatic selection estimates intent; it does not establish instance completeness.
Coverage and group-framing points remain pending when automatic scope acceptance
is unresolved. The user can correct or confirm the selection. Confirmation establishes
user intent only; it does not verify masks,
clear blur uncertainty, or dismiss an incomplete-mask warning.

Unconfirmed group framing records `measurement.blocked_by = "photo.coverage"`.
The score components retain that dependency and their separate weights. Details
groups coverage and its dependent framing findings under one selection blocker;
advice requests selection once. Even a small measured group waits for selection
before suggesting a framing change. Independent photo/product focus findings and
all earned, deducted, and withheld points remain separate and unchanged.

## Friendly photo review

The main result shows the original photo, saved verdict and score, a short readiness
summary, up to two supported strengths and at most three useful actions. Fully
supported all-clear results say **Your photo looks good.** Passing photos with
noncritical findings retain specific improvements. Incomplete checks never imply a
confirmed defect or an automatic need to retake the photo.

Actions cite their affected photo/product regions. Repeated findings for the same
issue and certainty are grouped; confirmed and incomplete findings stay separate.
Intended-selection and dependent framing uncertainty share one action, while focus
stays independent. Product cards retain every local blocker in plain language.
All-clear results do not invent generic preservation advice to fill an empty list.
The optional product-selection correction remains visible.

Measurements, calibration, provenance, point deductions, incomplete-check weights,
completion counts, and the technical score explanation are expandable under Details.
They are preserved evidence, not model-generated judgments. Historical reports
remain unchanged. This display update does not change inspection logic or scores.

## Groq contract and focused verification

`inspector/story.py` builds evidence-approved alternatives for the headline, summary,
supported strengths, and selected actions. The deterministic fallback picks the first
supported alternatives and is rendered immediately. Groq composes a short review
from those alternatives instead of repeating the raw diagnostic checklist. The
provider may choose wording and reorder strengths; backend action priority stays fixed.

Every returned phrase must match a supported alternative for its exact claim and
finding references. The validator rejects invented, moved, missing or duplicated
claims, altered certainty/scopes, changed priority, extra fields, verdict overrides,
and unsupported free text. Structured-output schema validation alone is not trusted.
The backend supplies the score, verdict and raw diagnostic wording independently;
provider output can update only the friendly story text. This deliberately bounds
paraphrasing rather than claiming to verify arbitrary natural-language entailment.

Missing configuration, timeouts, network/provider errors, malformed output and
unsupported claims retain the entire clear local story. The photo, filename, profile
paths and secrets are not sent. Browser updates use `textContent`. Relevant tests mock
provider responses; the browser verifier supplies validated alternatives locally and
makes no live Groq requests.

Run the current focused checks and layout verifier:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/test_story.py tests/test_friendly_results.py tests/test_narration.py tests/test_score_intent.py tests/test_current_repairs.py tests/test_intended_selection.py tests/test_audit_improvements.py tests/test_web.py tests/test_score_policy.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m scripts.verify_current_repairs friendly
```

The layout verifier uses an isolated database and local saved native photos. It checks
1360 px desktop and 390 px mobile, supported all-clear, passing improvements, confirmed
problems, hidden/missing/disabled checks, independent focus, shared selection blockers,
and corrected versus reprocessed history. Evidence is under `reports/friendly-results/`.

Earlier release contracts are retained below for their original controls.

Run the focused contracts:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_audit_improvements.py tests/test_score_policy.py tests/test_score_intent.py tests/test_photo_scope.py tests/test_one_click.py tests/test_multi_product.py tests/test_narration.py tests/test_storage.py tests/test_web.py tests/test_profiles.py -q
.\.venv\Scripts\python.exe -m scripts.verify_audit_improvements
```

The 1.7 browser verifier requires the local native single/multi-photo fixtures. It
uses an isolated temporary-port server/database, checks 1360 px desktop and 390 px
mobile, exercises native marking and count recovery, blocks explanation requests,
and preserves the main database. Screenshots and assertions are saved under
`reports/audit-improvements/`. No provider request or independent accuracy claim is
part of that run.

Older `verify_score_intent` and `verify_results_ui` scripts retain earlier-release
assertions and snapshots under their original report directories. Use the commands
above for 1.7. Earlier controls remain integration evidence, not natural-image
accuracy or calibration evidence across product categories.
