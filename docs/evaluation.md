# Evaluation and limitations

Framecheck is an assisted-review course prototype. Software checks, mask overlap, and defect-detection accuracy are different kinds of evidence. None should be reported as an overall marketplace acceptance score.

## Current inspection flow

The normal-resolution pipeline is `1.2.0-photo-scope-dip`. It runs independent photo and numbered-product checks, requires confirmation of intended products, supports marking/exclusion, and evaluates the intended group's framing. DIP assigns findings and the final verdict. Optional Groq output is restricted to approved wording.

The declared group-framing policy reuses demo constraints: dominant bounding-box extent 55–90%, centre offset at most 10%, and margins at least 2.5%. These are layout rules, not official marketplace requirements.

## Recorded software verification

The whole-photo repair recorded **128 focused passing tests** and **258 full-suite passing tests**. Fifteen live HTTP result cases and desktop/390 px mobile Chrome checks passed. All 43 reports present at that repair's snapshot retained their original JSON hashes. These are behaviour and compatibility results, not inspection-accuracy measurements.

Verified examples included a single shoe that passed after intent confirmation; a two-person image retaining four garments, where selecting only the two tops left three blur Reviews; a clipped product mask that stayed Review after confirmation; and a controlled merged pair that passed after marking individual items and excluding the merged candidate.

The latest repair's live Groq request was network-blocked. Local fallback and a successful mocked provider contract passed. An earlier successful live-provider report does not establish current connectivity.

## GitHub source preparation

The cleanup established a 258-test baseline, then passed **261 local tests** after portability checks and a procedural single-product regression were added. After isolating the incomplete-mask regression from the optional model, its affected 31-test gate passed again.

A clean preview containing only the 91 publication files passed **250 tests, with 11 optional private-photo regressions skipped**. This preview contained no private photos, database, `.env`, pretrained weights, or generated evaluation reports. A separate HTTP smoke check verified upload, intent confirmation to Pass, saved-parent preservation, history, guarded-profile integrity, and local explanation fallback without a provider request.

The preparation snapshot's **87 existing saved reports** retained their JSON hashes. Secrets, local weights, runtime thresholds, frozen core files, and original calibration records were preserved. Old test/browser files and bytecode caches were archived in an ignored local cleanup folder for recovery. The public source set is approximately 0.9 MB; local datasets and course evidence were not deleted to achieve this size.

Windows/Ubuntu GitHub Actions jobs are configured, but have not been executed on GitHub as part of this local preparation. These checks establish packaging and behaviour, not new inspection accuracy.

## Historical inspection accuracy

The original course selection contained 300 Fashion Product Images Small sources across apparel, footwear, bags, and watches. A frozen revised benchmark contained 1,137 original/derived records, including a 337-record test from 60 source groups.

| Frozen method | Macro-F1 | Defect recall | False rejects among seven acceptable cases |
| --- | ---: | ---: | ---: |
| Demo | 0.5043 | 50.49% | 1/7 |
| Calibrated | 0.5765 | 57.07% | 7/7 |
| Edge-supported comparison | 0.5171 | 52.93% | 1/7 |

The edge-supported method was a rejected comparison, not a runtime option. Calibration failed the accuracy goals despite improving some headline metrics. Only seven acceptable groups gave weak false-reject evidence.

A later guarded small-photo comparison used **448 records from 32 unseen source groups**, with development sizes of 900 training and 660 validation records:

| Check | Demo recall | Guarded recall | Guarded F1 | Known positive / negative records |
| --- | ---: | ---: | ---: | ---: |
| Blur | 2% | 63% | 0.7730 | 100 / 50 |
| Contrast | 36% | 44% | 0.6111 | 50 / 44 |
| Noise | 40% | 86% | 0.9247 | 50 / 0 |
| Underexposure | 50% | 50% | 0.6494 | 50 / 50 |
| Overexposure | 0% | 0% | 0.0000 | 50 / 44 |

Only definitive Fail counts as detecting a defect in these primary metrics. Unknown truth is excluded per check; a true defect referred to Review remains a miss. Noise has no known negative test labels. No fully labelled acceptable twelve-check images exist in that later test, so overall false-reject rate is unavailable. These are small-photo results, not current normal-resolution field accuracy.

## Masks and current calibration gaps

The earlier classical pipeline reported mean IoU 0.8802 over 195 AI reference records from 20 source groups. References were approximate and not independently human verified. This is not a SegFormer instance-accuracy score.

The normal-resolution pilot initially fitted thresholds on four source groups and checked two other apparel groups. Its holdout informed safety guards and cannot then be called a new untouched validation set. A nine-source native blur recheck produced five photo Passes/four Reviews and ten product Passes/five Reviews on originals. All nine severely blurred photo controls and all fifteen corresponding product regions received Review under the guarded Fail policy.

Numerical blur cutoffs remain frozen. Local tonal support, clipping, weak-foreground evidence, and adjacent-extreme mask warnings are limited heuristics. A Pass on a warning check means that warning did not trigger, not that its full quality or boundary accuracy is calibrated. Human mask approvals remain 0/6 in the earlier pilot, with no verified IoU and a blank/missing rack reference.

## What remains planned

- Independent human labels for native clear and naturally defective images.
- Human-verified product identities and masks, including touching and overlapping items.
- Negative examples, fully acceptable photographs, more product categories, and an independent footwear holdout.
- Source-group-disjoint holdouts at the actual 512 px analysis scale before stronger acceptance or new blur Fail cutoffs.

Grouping and exact/perceptual duplicate checks reduce leakage; they cannot prove that every related product family was identified. Synthetic variants remain correlated and do not establish natural-defect accuracy.

## Local provenance

The detailed historical reports and photos remain in the author's ignored local workspace, rather than being redistributed with private uploads and browser artifacts. The figures above summarize `reports/revision-phase6/findings.md`, `reports/quality-v3/findings.md`, `reports/multi-product-debug/blur-validation/results.json`, and `reports/whole-photo-repair/findings.md`. Frozen runtime thresholds and the guarded profile's development record are included under `data/`. Reproducing historical accuracy runs requires the separately retained datasets and evidence bundle.
