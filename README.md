# Framecheck

**HSR-AIML-056 ? Automated E-commerce Image Quality and Compliance Inspector**

Framecheck is a local Python application for reviewing product photographs. It checks the whole photo and each product region it can locate, then shows one **DIP verdict**, numbered findings, and practical advice.

The project demonstrates explainable **Digital Image Processing (DIP)**: colour-based segmentation, morphology, filtering, sharpness, contrast, noise, exposure statistics, and framing measurements. A pretrained SegFormer can help locate worn garments. Optional Groq wording explains existing findings; **DIP owns every finding and the verdict**.

> This is a course prototype with provisional thresholds. Pass means the applicable implemented rules passed; it is not a marketplace approval or a guarantee of inspection accuracy.

## What it checks

| Scope | Checks |
| --- | --- |
| Whole photo | Native resolution, sharpness, contrast, noise, and severe dark/highlight detail loss |
| Each numbered product | Pixel support, sharpness, noise, tonal support, clipping warnings, possible incomplete masks, and frame-edge contact |
| Intended product group | Overall size, placement, and margins in original frame coordinates |
| Coverage | Advisory foreground accounting, separate from required intended-item confirmation |

The app preserves original dimensions and uses an analysis image with a maximum long side of 512 px. Product exposure statistics use the selected mask. Mostly blank backgrounds receive a contrast guard rather than an automatic contrast rejection. Individual products do not each have to be centred.

## Workflow

```mermaid
flowchart TD
    A[Upload JPEG or PNG] --> B[Validate, orient, and preserve native pixels]
    B --> C[Whole-photo DIP checks]
    B --> D[Prepare analysis image]
    D --> E[Classical foreground segmentation]
    D --> F[Optional local SegFormer for supported clothing]
    E --> G[Number regions and run per-product DIP checks]
    F --> G
    G --> H[Show findings; mark missing items or exclude incidental ones]
    H --> I[Confirm intended items and assess group framing]
    C --> J[DIP aggregation: Fail, Manual Review, or Pass]
    G --> J
    I --> J
    J --> K[Save inspection and DIP evidence]
    K --> R[Display verdict and local explanations]
    R -. Approved finding text only .-> L[Optional server-side Groq request]
    L --> M[Validate wording; keep local fallback on failure]
    M --> N[Update explanation text only]
```

A required **Fail** takes precedence. Otherwise, unresolved required evidence produces **Manual Review**. **Pass** requires all applicable required checks to pass. Excluded items retain their observations but do not decide the verdict. Advisory foreground warnings can remain visible after confirmation.

## Run locally

Use **Python 3.12 or newer** and run from the repository root. The standard inspection flow needs no API key, dataset download, or neural-model weights.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py --port 8505
```

### Linux or macOS

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py --port 8505
```

Open **http://127.0.0.1:8505/**. The application creates its local SQLite database when an inspection is saved. Restart the Python process after changing backend code.

## Inspect a photo

1. Upload or drop a nonanimated JPEG/PNG, up to 10 MiB and 24 megapixels.
2. Read the completed whole-photo and numbered-product findings.
3. Uncheck incidental items, such as trousers in a tops-only listing.
4. Mark missing products. If a candidate merges several items, mark them separately and exclude the merged candidate.
5. Confirm that every intended product is numbered. New inspections remain Review until this step; a supported single-product result can then Pass.
6. Open **Details** for masks, scores, thresholds, calibration notes, or the downloadable JSON report.

Confirmation establishes user intent. It **does not verify mask accuracy** or clear outstanding quality/mask Reviews. Marking and confirmation save new child inspections; earlier reports remain unchanged. Advanced category/profile tools are available at `/?advanced=1`.

## First-time setup notes

- Ensure Python 3.12 or newer is installed.
- Run the commands from the repository root directory.
- Keep the virtual environment active by using its Python executable for subsequent commands.
- The application runs locally and can be accessed through your browser.
- No Groq API key or pretrained model weights are required for the standard inspection workflow.
  
### Optional clothing localization

Plain-background foreground inspection works without SegFormer. To enable the existing local clothing parser, install the optional garment dependencies and supply the matching checkpoint weights as described in [models/README.md](models/README.md).

The weights are not included in Git. No model is automatically downloaded or trained. Without suitable localization, available photo findings remain visible and the user can mark a product.

## Optional Groq explanations

Use [.env.example](.env.example) as the template for a local `.env` file:

```dotenv
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
```

Enter your own key only in the ignored `.env` file. The server reads settings when explanations are requested. Nonempty process environment settings take precedence.

Groq receives approved finding IDs, statements, and fixes?not the photo or filename. Responses must match every approved item exactly. Missing configuration, invalid claims, timeouts, and network/provider errors retain local wording and the saved DIP verdict. The app works without Groq.

## Tests

Using the virtual environment's Python:

```bash
python -m pip install -r requirements-dev.txt
python -B -m pytest -q -p no:cacheprovider --basetemp .pytest-work
```

Most tests generate their own images, masks, and temporary databases. Eleven optional regressions use local private shoe photos and are skipped when those files are absent. No API key, pretrained weights, private database, or downloaded dataset is needed for the public test suite.

GitHub Actions is configured to run the public suite on Windows and Ubuntu with Python 3.12. See [evaluation and limitations](docs/evaluation.md) for the distinction between software verification and inspection accuracy.

## Project layout

```text
app.py                   Local application entry point
inspector/               DIP measurements, localization, decisions, UI, storage, narration
scripts/                 Dataset, calibration, evaluation, and verification tools
tests/                  Procedural regression tests and optional local-photo checks
data/                   Published runtime configuration; ignored local datasets/database
models/                 Checkpoint metadata; ignored optional weights
docs/evaluation.md      Results, evidence boundaries, and remaining validation gaps
```

The research scripts preserve the course's earlier experiments. Dataset generation, historical comparisons, and live-browser evidence scripts require their separately retained local assets; they are not part of first-run setup.

## Data, evaluation, and limits

The default flow uses a provisional normal-resolution policy. Blur Fail ranges remain guarded because the available holdout exposed false failures. Local contrast/clipping and incomplete-mask checks are conservative warnings, not broadly calibrated defect classifiers.

Historical small-photo evaluation improved blur recall from 2% to 63% on known labels, but used AI-reviewed labels and synthetic variants. Those results do not establish current normal-resolution accuracy. Touching, hidden, small, or low-contrast products can still be missed or merged. Human-verified masks and a representative independent natural-defect dataset remain future work. Full details are in [docs/evaluation.md](docs/evaluation.md).

The publication includes only the small runtime policy and guarded-profile records under `data/`. Uploaded photos, SQLite databases, raw datasets, generated reports, browser profiles, course-export bundles, pretrained weights, and local cleanup backups remain ignored. See [data/README.md](data/README.md).

For first publication, use the [GitHub publishing guide](docs/github-publishing.md). Local course evidence and existing saved inspections do not need to be deleted or uploaded.
