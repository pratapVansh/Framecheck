# Whole-photo DIP quality score

New one-click reports use `1.4.0-results-intent-dip`. The backend saves a top-level
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
- Verdict retains required-findings precedence: Fail, Manual Review, Pass. A score
  with withheld points cannot claim Pass. There is no invented numeric approval cutoff.

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
A candidate is incidental only when **all** conditions hold:

1. It contacts or approaches within 0.5% of a photo edge.
2. Its area is at most 55% of the largest complete anchor's area.
3. Its area is less than 60% of the largest detected region's area.
4. Its shorter normalized bounding-box extent is at most 30% of the frame.
5. Its maximum normalized center offset is at least 20%.

Dominant cropped products, lone cropped products, and ambiguous candidates stay
intended and retain their cropping finding. Complete full-length products remain
eligible. The saved `intent_evidence`, `intent_reason`, `intent_source`, and
`intent_ambiguous` fields explain each selection. Excluded regions retain numbering,
local findings, and original evidence. Only intended products receive numbered markers
on the original photo. Incidental regions appear as unnumbered previews under Details.

The optional override expands the existing intended-item and marking controls.
Automatic selection estimates intent; it does not establish instance completeness.
Coverage and group-framing points remain withheld until the user confirms intended
numbering. Confirmation establishes user intent only; it does not verify masks,
clear blur uncertainty, or dismiss an incomplete-mask warning.

## Groq contract and focused verification

Groq receives backend-approved finding text plus score components, fixed weights,
finding references, deduction reasons, verdict, and coverage. It returns the exact
backend-approved `score_explanation` alongside finding-bound statements. Missing,
changed, or invented score claims reject the provider response and use local wording.
Provider output never changes the saved score, findings, or verdict. Photos, filenames,
and secrets are not included in the request body.

Run the focused contracts:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_score_intent.py tests/test_photo_scope.py tests/test_one_click.py tests/test_multi_product.py tests/test_narration.py tests/test_storage.py -q
.\.venv\Scripts\python.exe -m scripts.verify_score_intent
```

The second command uses the existing private two-T-shirt photo and native procedural
full-length/dominant-cropped controls. It asserts selection, cropping, scoring,
storage roundtrip, immutable override parents, and protected production-history/policy
hashes. Outputs stay local under `reports/score-intent-verification`. The controls
prove integration behavior, not natural-image accuracy across product categories.
Local checkpoints and the private photo are required for this optional verification.

`python -m scripts.verify_results_ui` additionally checks real Chrome uploads to
the running app on port 8505, using this photo and a deliberately cropped red-shirt
control. It checks desktop/mobile layouts, consecutive markers, hidden incidental
context, working evidence links, a saved override, and historical JSON hashes.
`reports/results-redesign/before.json` must contain the preservation snapshot.
