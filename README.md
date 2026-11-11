# Framecheck

### Understand your product photo before you publish it.

Framecheck reviews a product photograph, shows what looks good, and points to the areas that need attention. It combines explainable **Digital Image Processing (DIP)** with optional garment localization and friendly, evidence-grounded explanations.

**Runs locally · Reviews multiple products · Preserves inspection history · Works without an API key**

> **Course prototype · HSR-AIML-056:** Automated E-commerce Image Quality and Compliance Inspector. Its thresholds and score weights are provisional. A passing result means the configured review requirements were met; it is not a marketplace approval or a guarantee that every defect was detected.

[Get started](#get-started) · [Use Framecheck](#use-framecheck) · [Understand your result](#understand-your-result) · [How it works](#how-it-works) · [Optional features](#optional-features) · [Troubleshooting](#troubleshooting)

## What it does

Upload a photo and receive:

- **One result:** Pass, Needs Improvement, or Needs Review, with a score out of 100.
- **A short, natural summary:** whether the photo is ready, what looks good, and up to three useful actions.
- **Numbered product regions:** findings tied to the affected item or the whole photo.
- **An optional correction:** change which items belong in the review or mark a missing product.
- **Expandable evidence:** measurements, masks, score accounting, calibration limits, and inspection history.
- **A saved report:** revisit the result or download its JSON evidence.

When the evidence supports an all-clear result, Framecheck says **"Your photo looks good."** An incomplete check is explained separately from a confirmed problem.

### Checks at a glance

| Scope | What Framecheck examines |
| --- | --- |
| Whole photo | Original dimensions, focus, contrast, estimated graininess, and severe loss of dark or bright detail |
| Each intended product | Available detail, focus, graininess, tonal support, bright/dark-area warnings, possible incomplete outlines, and proximity to photo edges |
| Selected product group | How much of the original frame the group fills, its placement, and space around its edges |
| Product selection | Whether the detected and selected regions provide enough evidence to assess the intended products |

The default flow suits clearly separated products on reasonably plain backgrounds. The optional clothing parser extends localization to supported person-and-garment scenes. Manual marking remains available when automatic localization is uncertain.

## Get started

### Requirements

- **Python 3.12 or newer**
- A browser on the same computer
- Internet access to install dependencies; the standard local review does not require a provider connection

The core application needs **no dataset download, API key, pretrained weights, or training step**.

Run the following commands from the repository root.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe app.py --port 8505
```

### macOS or Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python app.py --port 8505
```

Open **[http://127.0.0.1:8505/](http://127.0.0.1:8505/)**.

These examples choose port **8505**. Without `--port`, the app uses **8501**. Stop the server with **Ctrl+C**. Restart it after application-code or model-weight changes. SQLite storage is created as needed.

<details>
<summary>Pinned dependencies and a separate inspection history</summary>

`pip install .` uses the dependency ranges in [pyproject.toml](pyproject.toml). [requirements.txt](requirements.txt) is the pinned snapshot verified on Windows with Python 3.14.5; install it instead when reproducing that environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

To use a separate database:

```powershell
.\.venv\Scripts\python.exe app.py --port 8505 --database data/demo-inspections.sqlite3
```

</details>

## First-time setup notes

- Ensure Python 3.12 or newer is installed.
- Run the commands from the repository root directory.
- Keep the virtual environment active by using its Python executable for subsequent commands.
- The application runs locally and can be accessed through your browser.
- No Groq API key or pretrained model weights are required for the standard inspection workflow.

## Use Framecheck

### 1. Upload an original photo

Choose **Upload a product photo** or drag a file onto the upload area. The standard page submits the selected image automatically.

| Input | Limit or behavior |
| --- | --- |
| Format | Nonanimated JPEG or PNG |
| File size | Up to **10 MiB** |
| Image dimensions | Up to **24 megapixels** |
| Orientation | EXIF orientation is applied before inspection |
| Transparency | Composited against white, with the conversion recorded |
| Grayscale | Converted to RGB, with the conversion recorded |

Use an original when available. Enlarging a small image does not restore its missing detail.

### 2. Read the result

The page brings the photo, saved verdict, score and short summary together. It shows supported strengths and at most three useful actions. Numbered markers connect the photograph to the product cards.

Each action identifies **Photo**, **Product 1**, **Product 2**, or the affected group. Related issues are grouped instead of repeating the same task. Incomplete checks do not confirm a defect. All-clear results do not add filler advice.

### 3. Correct the selected products only if needed

Use **Change products (optional)** if an incidental item was included or an intended item was excluded. Select at least one intended item, restore or uncheck regions as appropriate, then save the selection.

Reliable automatic selections do not require confirmation before a result. Selection uses relative prominence, geometry, edge contact and apparent completeness; it does not hard-code categories. Lone and dominant cropped products remain assessable.

### 4. Mark a missing or merged product

Open the selection controls, then **Mark a missing or merged product**.

1. Mark the visible outline of the missing item.
2. When a candidate merges several products, mark each intended product separately.
3. Exclude the combined candidate if it would duplicate the separately marked items.
4. Select the intended items and confirm the resulting coverage.

**Confirmation establishes intent, not mask accuracy.** It cannot clear independent focus uncertainty or an incomplete-outline warning. Corrections create new child reports; earlier reports remain unchanged.

### 5. Explore evidence or saved history

Open **Details** for masks, measurements, score accounting, calibration and provenance. Use **Download report** for JSON or **Saved inspections** to revisit earlier results.

Advanced category/profile tools are available at **[/?advanced=1](http://127.0.0.1:8505/?advanced=1)** when using the example port.

### Everyday workflow

```mermaid
flowchart TD
    A[Upload a JPEG or PNG] --> B[Read photo, verdict, score and summary]
    B --> C{Does the product selection match your listing?}
    C -- Yes --> D[Follow the relevant suggestions]
    C -- No --> E[Change selected items or mark missing products]
    E --> F[Save a corrected child report]
    F --> D
    D --> G[Open Details when more evidence is useful]
    G --> H[Download the report or revisit saved inspections]
```

## Understand your result

| Verdict | Meaning | Next step |
| --- | --- | --- |
| **Pass** | The score meets the threshold, essential evidence is complete, and no required critical failure blocks approval | Use the result with its stated limits; apply any specific noncritical improvements |
| **Needs Improvement** | A confirmed critical failure exists, supported severe framing blocks approval, or a complete score falls below the threshold | Address the confirmed problems and review a suitable original again |
| **Needs Review** | Required evidence is incomplete or the score policy is invalid | Read the specific uncertainty and inspect the original or correct the selection as appropriate |

A high score can still need review. **86/100 with 14 points pending for focus** does not establish that the photo is blurry. It means focus has not been resolved confidently enough to approve it.

A passing photo can still have a noncritical improvement. Pass requires complete evidence, sufficient points and no critical blocker; it does not require every individual finding to be Pass.

### Score graph

This is the **declared point budget**, not measured accuracy or the percentage of image area inspected.

```mermaid
pie showData
    title Fixed 100-point review budget
    "Whole-photo checks" : 50
    "Intended-product checks" : 30
    "Group framing" : 10
    "Intended-product coverage" : 10
```

```text
Score = 100 - confirmed deductions - points pending incomplete checks
```

- Passing components earn points; failing components lose them.
- Incomplete, disabled or missing components earn no points while unresolved.
- Product components apply once across intended products, using the worst applicable state. Extra products do not multiply the 30-point product budget.
- Excluded regions retain evidence without determining product scoring.
- If one product fails a component and another is unassessed, the weight is deducted once; the unresolved finding still blocks approval.

The possible range adds pending points to the score. It is **not a statistical confidence interval**. Assessed coverage means decisive check weight, not product-detection recall, image area or verified mask accuracy.

### Verdict decision flow

The default Pass threshold is **75/100**.

```mermaid
flowchart TD
    A[Backend findings and fixed point budget] --> B{Valid score policy?}
    B -- No --> R[Needs Review]
    B -- Yes --> C{Confirmed critical failure or severe framing?}
    C -- Yes --> I[Needs Improvement]
    C -- No --> D{All essential evidence complete?}
    D -- No --> R
    D -- Yes --> E{Score at least 75?}
    E -- No --> I
    E -- Yes --> P[Pass]
```

This diagram uses the default threshold. Each inspection saves its effective policy. Invalid policy requests Review before other gates. Supported severe framing blocks approval; mild isolated framing failures can remain ordinary deductions.

See [Quality score and evidence policy](docs/quality-score.md) for exact weights, critical checks and severity rules. Changing a threshold changes policy; it does not improve a photograph or establish inspection accuracy.

## How it works

### Architecture graph

```mermaid
flowchart LR
    subgraph Browser[Local browser]
        U[Upload and product correction]
        V[Photo, verdict, score and friendly review]
        D[Expandable Details]
    end
    subgraph App[Python application on localhost]
        W[HTTP handlers and validation]
        L[Foreground or garment localization]
        Q[Photo and product DIP measurements]
        S[Scoring and verdict policy]
        T[Evidence-grounded story builder]
        G[Groq response validation]
        C[Bounded localization and PNG caches]
    end
    DB[(Local SQLite history and image evidence)]
    API[Optional Groq API]
    U --> W
    W --> L
    L --> Q
    Q --> S
    S --> T
    T --> V
    Q --> D
    W --> DB
    DB --> V
    L <--> C
    W <--> C
    T -. Approved text, references and score evidence .-> API
    API -. Proposed story wording .-> G
    G --> V
```

**DIP measures the image. Backend policy decides the result. Optional models assist localization or wording.**

### Image-processing flow

```mermaid
flowchart TD
    A[Uploaded image bytes] --> B[Validate type, limits and frame count]
    B --> C[Apply orientation and prepare native RGB pixels]
    C --> D[Whole-photo measurements at analysis scale]
    C --> E[Analysis image with long side at most 512 px]
    E --> F[Classical foreground segmentation]
    E --> G[Optional person-associated clothing parser]
    F --> H[Candidate regions in native coordinates]
    G --> H
    H --> I[Per-product measurements and outline warnings]
    I --> J[Estimate intended versus incidental regions]
    J --> K[Assess intended coverage and native group framing]
    D --> L[Fixed score budget and verdict gates]
    I --> L
    K --> L
    L --> M[Save report, original, mask and overlay]
    M --> N[Render immediate local summary]
    N -. Optional wording request .-> O[Validate Groq story or retain local fallback]
```

| Stage | Technique | Boundary |
| --- | --- | --- |
| Input preparation | Pillow decoding, orientation handling, RGB conversion and size limits | Original dimensions are preserved |
| General localization | Lab border-color estimation, thresholding, morphology, connected components and guarded GrabCut refinement | Foreground regions are not verified product identities |
| Clothing localization | Optional local pretrained SegFormer associated with person-scene evidence | Semantic garment labels do not prove separate product instances |
| Focus and detail | Laplacian/Sobel measurements and usable-pixel support | Unsupported Fail ranges and uncertainty bands remain Review |
| Contrast and exposure | Tonal percentiles, dark/bright fractions and severe detail-loss indicators | Natural black, white or uniform material can resemble a problem |
| Graininess | Filter responses and robust statistics in comparatively flat regions | An estimate, not a calibrated camera-noise model |
| Framing | Selected group bounds in original frame coordinates | Each individual product need not be centered |
| Explanation | Supported wording with finding references and certainty | Generated text cannot change findings or approval |

Mostly blank backgrounds do not automatically establish poor product contrast. A narrow supported focus rule can use passing product checks while retaining the original whole-photo observation. It does not clear unresolved product focus or calibration uncertainty.

### Who owns each decision?

| Component | Responsibility | Does not own |
| --- | --- | --- |
| Classical DIP | Measurable image evidence and region-quality checks | Guaranteed product identity |
| Optional SegFormer | Candidate clothing masks | Scores, verdicts, verified instances or human-approved outlines |
| Backend policy | Applicability, fixed weights, critical gates and the saved verdict | Marketplace approval |
| Optional Groq | Friendly wording from supplied evidence-approved alternatives | New findings, causes, scores, verdicts or unsupported advice |
| User correction | Which visible products should be included | Proof that a mask or an uncertain quality check is correct |

## Corrections, reinspection and history

A correction creates a **new child inspection**, without overwriting its parent.

| Operation | Retained | New |
| --- | --- | --- |
| Change selected products | Original measurements, saved profile and existing evidence | Applicability, selected-group assessment, score and correction/scoring provenance |
| Mark a product | Inherited measurements and saved profile | Added-region measurements/version and any new mask/overlay evidence |
| Reinspect the original | Saved original image and a link to the earlier report | Current rules/profile and a current inspection; valid localization may be safely reused |
| Reopen a saved report | Saved findings, policy, verdict and measurement history | Current presentation and grounded wording; no retrospective score recalculation on read |

Details distinguishes **Corrected selection** from **Reprocessed original**. Measurement, profile, correction and scoring versions are separate. Missing origins in older derived reports are disclosed, not relabelled as current measurements.

## Optional features

### Local clothing localization

The standard foreground flow works without neural weights. For supported worn-garment scenes:

1. Install the garment extra using the virtual environment:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install ".[garment]"
   ```

   On macOS/Linux, use `.venv/bin/python` instead.

2. Follow [the checkpoint setup guide](models/README.md) to obtain the matching local safe weights.
3. Place them at `models/segformer_b2_clothes/model.safetensors` and restart the app.

The loader runs on CPU. It does not automatically download weights, train a model, or substitute random predictions when weights are missing. Garment masks remain estimated, unverified regions.

### Friendly Groq explanations

The local fallback is available immediately and requires no API key. To enable Groq wording, create or edit an ignored `.env` file using [.env.example](.env.example):

```dotenv
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
```

Add your own key after `GROQ_API_KEY=`. Nonempty process environment values take precedence. Settings are read when explanations are requested, so editing the local key does not require restarting the app.

```mermaid
flowchart LR
    A[Saved backend evidence] --> B[Supported readiness, strengths and action phrases]
    B --> C[Immediate deterministic local story]
    B -. Optional request .-> D[Groq composes friendly wording]
    D --> E{Exact supported claims, references and priority?}
    E -- Yes --> F[Update story text only]
    E -- No --> C
    D -. Timeout or provider failure .-> C
```

The provider can choose supported wording alternatives and reorder strengths. It cannot invent claims, swap product scopes, turn uncertainty into a defect, change action priority, or override readiness. Validation rejects extra, missing, duplicate, misplaced, or unsupported claims. This deliberately bounds phrasing rather than claiming to validate arbitrary free-form language.

The backend keeps scores, verdicts, raw findings, and the technical score explanation. Missing configuration, invalid output, timeouts, and network/provider failures preserve the complete local story.

## Privacy and storage

| Data or behavior | Handling |
| --- | --- |
| Uploaded photo and image evidence | Stay in the local application and SQLite storage; not sent to Groq |
| Optional explanation request | Includes referenced finding text, approved wording alternatives, and backend score evidence |
| Filename and local filesystem paths | Not included in the explanation request |
| API key | Used server-side for provider authentication; never included in the prompt or page |
| Saved history | Stored in `data/inspections.sqlite3` by default |
| Local assets and credentials | Photos, databases, reports, weights, browser profiles and `.env` are ignored by Git |

The bundled HTTP server binds to **127.0.0.1** and is intended for local use. It is not a production service with user accounts or multi-user access controls.

To preserve history, back up the database while the app is stopped. A fresh checkout does not include private photos, prior inspections, datasets, or model binaries. See [local data and runtime configuration](data/README.md).

## Performance and implementation notes

- DIP measurements use an analysis image with a maximum long side of **512 px**; native dimensions and coordinates are retained separately.
- Garment output uses native-coordinate bilinear logit interpolation. Class streaming reduces intermediate memory without replacing it with resized hard labels.
- Localization reuse is process-local, bounded to **four entries / 48 MiB of measured arrays**, and keyed by input, implementation and checkpoint signatures.
- A separate evidence cache holds at most **four entries / 48 MiB of immutable PNG bytes**, bound to the input and ordered masks.
- Fresh inspections recompute checks and scores even when localization or evidence is reused.
- Evidence uses lossless PNG compression. Faster compression may produce larger files.
- Details records processing, localization and evidence work. Upload/correction `Server-Timing` also measures storage and application request time.
- Cold Torch/Transformers imports and package scans can dominate the first garment inspection. A fast cached result does not imply a fast cold start.

Caches are cleared when the process restarts. They do not update old reports. Processing timing excludes subsequent browser rendering/network transfer and optional explanation generation. See [the detailed runtime policy](docs/quality-score.md) for cache, coordinate and timing contracts.

## Testing and verification

### Install development dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install ".[dev]"
```

Use `.venv/bin/python` on macOS/Linux. [requirements-dev.txt](requirements-dev.txt) provides the pinned development snapshot as an alternative.

### Run the public suite

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp .pytest-work
```

Most tests use generated images and temporary databases. Optional private-photo regressions skip when their local fixtures are absent. Neural-model-specific tests can also skip when their optional dependency is unavailable. Public checks do not require an API key, downloaded dataset, private inspection history, or model checkpoint.

### Run focused frontend and explanation checks

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/test_story.py tests/test_friendly_results.py tests/test_narration.py tests/test_score_intent.py tests/test_current_repairs.py tests/test_intended_selection.py tests/test_audit_improvements.py tests/test_web.py tests/test_score_policy.py -q -p no:cacheprovider
```

These cover supported all-clear language, scoped improvements, incomplete checks, strict provider validation, deterministic fallback, product selection, history, and backend-owned scoring. Provider behavior is mocked; passing these checks does not establish live Groq connectivity or inspection accuracy.

### Optional browser verification

```powershell
.\.venv\Scripts\python.exe -B -m scripts.verify_current_repairs friendly
```

This local verifier uses an isolated database and headless Chrome. It checks **1360 px desktop** and **390 px mobile**, including saved single- and multi-product photos, provider-text updates, evidence links and corrected/reprocessed history. It requires the separately retained native-photo records and the configured local Chrome installation. It uses a validated local provider double and makes no live Groq calls.

GitHub Actions is configured for Windows and Ubuntu with Python 3.12. Configuration is not a claim that a particular remote CI run passed.

## Project map

```text
app.py                         Local application entry point
inspector/
  web.py                       HTTP routes, uploads and report access
  modern_ui.py                 Responsive upload and result pages
  auto_inspect.py               Default detection, selection and inspection flow
  segmentation.py              Classical foreground segmentation
  garments.py                  Optional local garment model and native labels
  product_measurements.py      Scoped image and framing measurements
  scoring.py                   Fixed point budget and verdict gates
  story.py                     Friendly, evidence-approved story alternatives
  narration.py                 Groq request, validation and local fallback
  storage.py                   SQLite report and evidence storage
  profiles.py                  Advanced profile integrity checks
scripts/                       Evaluation, calibration and verification tools
tests/                         Unit, regression and HTTP contracts
data/                          Published runtime configuration; ignored local data
models/                        Checkpoint metadata; ignored optional weights
docs/                          Policy, evaluation, annotation and publication guides
```

NumPy, OpenCV and Pillow support image processing. The local web server and SQLite integration use Python's standard library. scikit-learn supports evaluation tooling. Torch, Transformers and safe checkpoint weights are optional for garment localization.

Research scripts preserve earlier course experiments. Their separately retained datasets and assets are not first-run application requirements.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| The example URL does not open | Keep the server terminal running. Match the URL to `--port`; the default without that argument is 8501 |
| The port is already in use | Start with another port, such as `--port 8506`, then open the matching URL |
| No product was located | Read the available photo checks, then mark a visible intended product; complex scenes may need the optional parser |
| An incidental item was counted | Use **Change products (optional)** and save the intended selection |
| Two touching products became one region | Mark them separately, exclude the combined candidate, and confirm the intended selection |
| A high score still says Needs Review | Read the incomplete check. Pending evidence and confirmed defects are different; confirming selection cannot resolve independent focus uncertainty |
| Groq wording is unavailable | Check `.env`, process-environment precedence, and the explanation source under Details. Local wording remains available |
| The first garment review is slow | Dependency/model initialization can dominate cold processing. Compare localization and encoding timings under Details |
| A saved correction still shows older measurements | This preserves provenance. Reinspect the original to establish current measurements and profile |
| An advanced profile fails integrity checks | Restore its matching original profile/calibration/core files; the loader deliberately rejects changed calibration provenance |
| The file is rejected | Check JPEG/PNG format, animation, 10 MiB size and 24 MP limits. Prefer an appropriate original instead of enlarging a small file |

The local health endpoint is `http://127.0.0.1:8505/health` when using the example port.

## Evaluation and known limits

Framecheck demonstrates explainable assisted review. **Software correctness, localization overlap, defect-detection accuracy and marketplace acceptance are different claims.**

- Thresholds, fixed weights and composition rules are provisional, not broadly validated marketplace standards.
- Touching, overlapping, hidden, small or low-contrast products can be missed or merged. Geometry cannot establish listing intent with certainty.
- Product masks are estimated. A passing outline warning does not mean a human verified the mask.
- Blur Fail ranges remain guarded because earlier holdouts exposed false failures. Review is intentional where the evidence cannot support a definitive judgment.
- Natural black, white or uniform material can resemble clipping or lost detail; local warnings are conservative.
- Independent human-labelled natural defects, verified product instances/masks, and broader category coverage remain needed.
- Cold-start and large-image/concurrent-load behavior need broader benchmarking. Small local timing samples are not a performance guarantee.
- Groq phrasing is restricted to supported alternatives. Provider availability does not affect the saved DIP result.

Historical small-photo experiments reported improvements on their own datasets, including synthetic variants and AI-reviewed labels. Those results do **not** establish current normal-resolution field accuracy. The [evaluation record](docs/evaluation.md) contains earlier pipeline snapshots and their evidence boundaries; use this README and the [current quality policy](docs/quality-score.md) for present behavior.

## Further reading

| Guide | Purpose |
| --- | --- |
| [Quality score and evidence policy](docs/quality-score.md) | Weights, verdict gates, intended scope, provenance, caching and explanation contracts |
| [Historical evaluation and limitations](docs/evaluation.md) | Earlier experiments, validation scope and remaining data requirements |
| [Annotation rubric](docs/annotation-rubric.md) | How reference labels and uncertainty should be recorded |
| [Optional checkpoint setup](models/README.md) | Local garment dependencies, metadata and weight placement |
| [Runtime configuration and local data](data/README.md) | Published configuration versus ignored private artifacts |
| [GitHub publishing guide](docs/github-publishing.md) | Preparing the source without distributing private data or secrets |
