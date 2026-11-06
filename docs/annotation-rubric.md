# Annotation rubric — Demo White Background v1

Inspect the native image, then zoom for visibility. Record every visible defect independently. Use **Uncertain** when evidence is insufficient; do not infer quality labels from category metadata, generator settings, or detector output.

## Review provenance

The user selected AI-only review for the initial demonstration. A completed AI review sets `reviewed=true`, `review_method=ai_visual_review`, and `human_verified=false`; a source-checked AI mask sets `mask_reviewed=true`, `mask_review_method=ai_visual_review`, and `mask_human_verified=false`. These flags do not establish human confirmation. The actual AI reviewer, date, evidence, corrections, and unknown labels must remain traceable. AI mask boundaries are approximate at this resolution. All future reports must disclose this annotation source and cannot claim independently human-verified performance. A person who actually checks the image and submits the review form can subsequently record a human review.

## Photo context

Choose **Product only**, **Product worn or held by a person**, or **Not sure** for every image. This field records the reviewer's observation; the application does not automatically recognize people. Missing legacy context is uncertain.

Automatic product-dependent checks support isolated-product context only. For model-worn images, label the garment itself where its boundaries and details can be established. Record Uncertain for product-specific checks that cannot be assessed. A person's head, hands or cropped legs must not define a garment's frame coverage, margins or cropping label. Native resolution remains independently assessable in every context.

## Technical quality

| Defect | Present | Absent / uncertain considerations |
|---|---|---|
| Blur | Product detail or meaningful edges are visibly smeared relative to an interpretable product photograph | Low-detail or smooth products may be uncertain. Do not call lack of texture blur automatically. |
| Low contrast | Product tonal detail is visibly compressed, making meaningful structure hard to distinguish | Naturally uniform products may be uncertain; a plain background is not evidence of contrast failure. |
| Noise | Visible random grain or speckles obscure the product | Fabric weave, prints, stitching, and compression artifacts can resemble noise. Mark uncertain when attribution is unclear. |
| Underexposure | Important product details disappear into dark tones | Black products can be correct. Judge lost detail, not darkness alone. |
| Overexposure | Important product details disappear into clipped highlights | A white background is expected. White products require enough visible structure to assess. |
| Insufficient resolution | Native short side is below 60 pixels or native long side is below 80 pixels | Evaluate decoded dimensions, never a zoomed display or an upscaled preview. |

## Rule compliance

| Defect | Present | Uncertain considerations |
|---|---|---|
| Background color | Clearly nonwhite background violates the stated white-fraction rule | Shadows, edge mixing, or a poorly separable white product can make the background region uncertain. |
| Background uniformity | Noticeably patterned, textured, gradient, or inconsistent background violates the stated spread rule | Product pixels and soft shadows should not be confused with a background pattern. |
| Frame coverage | Product bounds occupy less than 55% or more than 90% along the dominant normalized axis | Judge the whole intended product, including legitimate disconnected parts. |
| Placement | Product bounding-box center exceeds the 10% offset rule on either axis | This is bounding-box placement, not image center of mass. |
| Margins | At least one product-to-frame margin is below 2.5% of that axis | One-pixel boundary cases at these small dimensions are uncertain. |
| Possible cropping | Visible truncation or missing product structure is established | Edge contact alone cannot prove cropping; record uncertainty when missing content is not established. Person cropping is not evidence of garment cropping. The detector routes edge contact to review. |

## Annotation procedure

1. Review the original without detector predictions. Confirm photo context and record every category as Present, Absent, or Uncertain.
2. Write notes for difficult exposure, blur/texture, grouping, and edge-contact cases.
3. Assign related photographs and confirmed perceptual duplicates the same product group before split assignment.
4. Enter the reviewer name and confirm that every category was reviewed.
5. For masks, mark the visible product white and background black at native dimensions. Preserve legitimate holes and disconnected product parts. Do not fill the bounding box as a substitute for tracing the product.
6. Independently verify every mask before checking its confirmation box. Generated/transformed masks are not reference masks until reviewed.
7. Review generated variants afresh. Inherited and intended-transform labels are suggestions. Correct incidental defects and failed transformations.

An image is acceptable for false-reject evaluation only if all twelve labels are explicitly Absent. Any Uncertain label prevents assuming acceptability. Record subjective or unresolved examples rather than forcing clean labels.

Test data is for final evaluation. Complete test annotation independently, and never use test images/masks to choose thresholds or segmentation parameters.

## Cropping evaluation policy

The cropping check is an advisory referral, not a definitive rejection classifier. Primary metrics count only Fail as a positive detection. A Manual Review on a true cropping case therefore remains a miss in primary defect recall and cropping F1/recall. Those primary cropping scores are zero while the check never emits Fail.

Reports separately count true cropping cases referred for review and referral recall. Context or segmentation uncertainty may also cause referrals; these counts must not be presented as definitive cropping detection or rejection accuracy. The primary macro-F1 still uses the fixed twelve categories.
