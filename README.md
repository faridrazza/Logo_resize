# Logo Resize Service — 300 × 300, without changing the logo

Takes any logo (square, horizontal or vertical) and outputs it at **300 × 300 px** with the
artwork untouched: no stretching, no cropping, no re-typesetting, no colour shift.

## How it guarantees "the logo must not change"

| Step | What happens |
|---|---|
| 1. Deterministic fit | One uniform scale factor `min(300/w, 300/h)` applied to **both** axes (LANCZOS), then centred on a 300 × 300 canvas. Distortion is mathematically impossible — there is a single scale variable. |
| 2. Padding | Transparent if the source has alpha, otherwise the source's own background colour (corner-detected). Never a new white card behind a transparent logo. |
| 3. Image model | The composed square render is sent to the image model with `prompt.txt`, a strict reproduction prompt (no redesign, no re-typeset, no colour correction, no cleanup, no effects). |
| 4. Fidelity gate | The model output is scored against the deterministic render: perceptual hash, SSIM, mean pixel difference, dominant-palette distance. |
| 5. Decision | `hybrid` (default) keeps the model output **only** if every gate passes; otherwise it silently falls back to the pixel-exact render. |

### Modes

| Mode | Behaviour | Verdict emitted |
|---|---|---|
| `exact` | Deterministic resize only. No model call, no cost, fastest. | `UNCHANGED` |
| `ai` | Image model output, always used. Metrics reported for review. | `UNCHANGED_VERIFIED` / `REVIEW` |
| `hybrid` *(default)* | Model output if it passes the gate, else the exact render. | `UNCHANGED_VERIFIED` / `UNCHANGED` |

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # then set OPENAI_API_KEY
python app.py                     # http://127.0.0.1:8000/docs
```

## Endpoints

`GET /api/v1/logo/health` — config and readiness.

`POST /api/v1/logo/resize` — one logo (multipart form).

| Field | Notes |
|---|---|
| `file` | the logo upload (png/jpg/webp/gif/bmp/tiff) |
| `image_url` | alternative to `file` |
| `mode` | `exact` \| `ai` \| `hybrid` |
| `return_image` | `true` streams the PNG instead of JSON |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/logo/resize \
     -F "file=@logo.png" -F "mode=hybrid"
```

```jsonc
{
  "renderer_used": "exact",
  "source":  { "source_width": 1200, "source_height": 300, "aspect_type": "horizontal" },
  "output":  { "width": 300, "height": 300, "format": "PNG" },
  "fidelity": { "phash_distance": 2, "ssim": 0.991, "mean_pixel_diff": 0.006, "passed": true },
  "verdict": "UNCHANGED",
  "image_base64": "..."
}
```

`POST /api/v1/logo/batch` — many at once (max 200).

```bash
curl -X POST http://127.0.0.1:8000/api/v1/logo/batch \
     -H "Content-Type: application/json" \
     -d '{"items":["acme.png","https://cdn.site/logo.svg.png"],"mode":"hybrid"}'
```

## The sheet test run

```bash
python scripts/fetch_sheet.py "Logos to be resize.xlsx"   # download the logos
python scripts/run_test.py --workers 6                    # both paths, all logos
python scripts/build_report.py                            # HTML + CSV
```

`run_test.py --reuse` re-measures the saved renders without calling the model again, so metrics
can be revised without re-billing the API.

Produces in `data/output/`:

* `exact/<name>.png`, `model/<name>.png` — the 300 × 300 outputs from each path
* `results.json` — every measurement
* `logo_resize_300x300_report.html` — self-contained shareable report, images embedded
* `report.csv` — same data for Google Sheets (**File → Import → Upload**)

### Result, 49 logos, 2026-09-16

| | Deterministic | gpt-image-2.5-flare |
|---|---|---|
| Unchanged | **49 of 49** | 11 of 49 |
| Part of the logo missing | 0 | 4 |
| Redrawn but complete | 0 | 34 |
| Time per logo | ~20 ms | ~22 s |

Every deterministic output is bit-identical to an independently computed uniform scale
(largest channel difference 0 of 255). The model re-typeset wordmarks, shifted brand colours,
and on four logos returned the icon with the wordmark deleted.

## Configuration (.env)

| Key | Default | Purpose |
|---|---|---|
| `TARGET_SIZE` | `300` | output edge length |
| `OUTPUT_BACKGROUND` | `auto` | `auto` \| `transparent` \| `white` |
| `ALLOW_UPSCALE` | `true` | scale small logos up to fill 300 px |
| `TRIM_BORDER` | `false` | keep `false` — trimming changes the logo's framing |
| `DEFAULT_MODE` | `hybrid` | `exact` \| `ai` \| `hybrid` |
| `IMAGE_MODEL` | `gpt-image-2.5-flare` | model id, change without touching code |
| `IMAGE_INPUT_FIDELITY` | `high` | passed to the model; auto-dropped if unsupported |
| `VERIFY_PHASH_MAX` | `6` | structural drift budget (0–64) |
| `VERIFY_SSIM_MIN` | `0.93` | minimum structural similarity |
| `VERIFY_PIXEL_DIFF_MAX` | `0.04` | max mean RGB difference (0–1) |
| `VERIFY_COLOR_DELTA_MAX` | `12` | max dominant-colour shift |

## Layout

```
app.py           FastAPI entrypoint
router.py        all routes (one router)
controller.py    request validation + response shaping (one controller)
service.py       resize pipeline, model call, mode logic
verify.py        pHash / SSIM / pixel / colour metrics
config.py        .env-driven settings
prompt.txt       the fidelity-preservation prompt
scripts/fetch_sheet.py   pull logo URLs out of the xlsx
scripts/run_test.py      run both paths, measure everything
scripts/build_report.py  results.json -> shareable HTML + CSV
scripts/run_batch.py     generic folder/URL batch through the endpoint
```
