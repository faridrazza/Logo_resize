# Logo squaring service

Implements the **Logo Squaring Rules, 19 September 2026**. Takes any logo (square,
horizontal or vertical) and pads it onto a square background so Google's Performance Max
logo slot will accept it. The logo itself is untouched: the square is the empty space
around it.

## The rule

```
INPUT   W = logo width in pixels
        H = logo height in pixels

STEP 1  If W and H cannot be read, STOP. Reason: unreadable.
STEP 2  L = the LARGER of W and H
STEP 3  EDGE = L;  if EDGE < 128 then 128;  if EDGE > 1200 then 1200
STEP 4  Make a canvas EDGE wide and EDGE tall, in the background colour.
STEP 5  Scale the logo by min(EDGE/W, EDGE/H), but NEVER above 1.0.
        Centre it on the canvas.

OUTPUT  A square PNG, EDGE x EDGE.
```

The output size is **derived per logo, not fixed**. Across the 93-logo test set this
produced 71 different sizes. A 600 × 200 logo becomes a 600 × 600 square, not
1200 × 1200 — reaching 1200 would stretch 600 px of detail across 1200 px of space.

`min(..., 1.0)` in STEP 5 is the whole safety rule: a logo is never enlarged, because
enlarging blurs it.

### The five checks

| | Check | Outcome |
|---|---|---|
| 1 | Can the file be read at all? | The only refusal there is |
| 2 | Is it already square? | To within 5%, not to the pixel |
| 3 | How big is the square going to be? | `clamp(max(W,H), 128, 1200)` |
| 4 | Which background colour? | Filename → pixels → white |
| 5 | Make it | Pad, never crop, never stretch |

There is **no size at which a logo is refused**. Google's 128 px minimum applies to the
image, not to the logo inside it: a 40 × 20 logo is centred on a 128 × 128 square.

### Pad, never crop, never stretch

One scale factor is applied to both axes, so the artwork cannot be squashed, and the
canvas is never smaller than the scaled artwork, so nothing can be cut off.

### The background colour

Decided per logo, in this order:

1. **What the file says about itself** — `logo-on-dark`, `logo-white`, `logo-reverse`.
2. **What the pixels say** — and this splits in two, because the two cases pull opposite
   ways:
   * a **transparent** source is ink and nothing else, so it needs the *contrasting*
     plate or it disappears;
   * an **opaque** source already sits on a plate its designer chose, so the padding must
     *continue* that plate. A dark wordmark on its own white rectangle, padded with
     near-black, becomes a white rectangle floating on black.
3. **White**, when neither gives an answer.

Only two colours are ever used — `#ffffff` and `#0d0d0d`. No brand-colour matching.
Transparency is always flattened onto the chosen colour, so the square the business sees
is the square Google receives.

## The endpoint contract

`POST /api/v1/logo/square`

```jsonc
{
  "logo_url": "https://cdn.site/logo.png",
  "size": 600,            // optional; computed from the rule when omitted
  "background": "#ffffff" // optional; chosen per logo when omitted
}
```

The size is worked out **before** the call and sent as an instruction. When an image comes
back its dimensions can be checked, but not whether the renderer got there honestly — a
1200 × 1200 that was padded and a 1200 × 1200 that was enlarged from a 300 px original look
identical in their dimensions. The only defence is to never ask for a size that would
require enlarging.

### What is rejected on arrival

| Check | Rejected as |
|---|---|
| Width does not equal height | `not_square` |
| Size below 128 or above 1200, or not the size asked for, or over 5 MB | `out_of_range` |
| No image link in the response | `upstream_missing_output_url` |
| An error reported inside a 200 response | `upstream_error` |
| No response within 30 seconds | `upstream_unreachable_or_timeout` |

A rejection is never visible to the business: the service squares the logo itself and
carries on.

## The model path

`mode=ai` / `mode=hybrid` routes the square through the image model. The model is handed
the square **already composed**, so it is never asked to scale or centre anything — that
work is done before it is called.

With `COMPOSITE_SOURCE=true` (the default) the source artwork is pasted back over its own
rectangle at full resolution after the model replies, blended against the chosen plate
rather than against whatever the model drew underneath. The artwork in the shipped file is
then the source itself, measured at a largest channel difference of **0 of 255**.

This matters because the model does change logos when left to itself. Measured on the same
93 logos, its raw renders differ from the squared-in-code result by a median SSIM of about
0.79 before the composite. What the model still contributes afterwards is the background
plate and nothing else.

## Other endpoints

`GET /api/v1/logo/health` — config and readiness.

`POST /api/v1/logo/resize` — multipart upload (`file` or `image_url`), with optional
`size`, `background`, `mode`, `return_image`.

`POST /api/v1/logo/batch` — up to 200 URLs or local paths in one call.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # then set OPENAI_API_KEY
python app.py                     # http://127.0.0.1:8000/docs
```

## The sheet test run

```bash
python scripts/fetch_sheet.py "Sample logo for resizing.xlsx" --limit 100
python scripts/retry_failed.py                 # SVGs and hotlink-protected sources
python scripts/run_test.py --workers 6         # both paths, all logos
python scripts/build_report_plain.py           # HTML + CSV
```

`retry_failed.py` rasterises SVGs through `svglib`, which needs the `rlPyCairo` backend
installed or every SVG row fails.

`run_test.py --reuse` re-measures the saved renders without calling the model again;
`--fill` calls it only for logos with no cached render.

Produces in `data/output/`:

* `exact/<name>.png`, `model/<name>.png` — the squared outputs from each path
* `results.json` — every measurement
* `logo_squaring_<n>_logos.html` — self-contained shareable report, images embedded
* `report_plain.csv` — same data for Google Sheets (**File → Import → Upload**)

## Configuration (.env)

| Key | Default | Purpose |
|---|---|---|
| `EDGE_MIN` | `128` | Google's floor for the image |
| `EDGE_MAX` | `1200` | Google's recommended size; nothing above helps |
| `SQUARE_TOLERANCE` | `0.05` | how close to square counts as square |
| `BG_LIGHT` | `#ffffff` | the light plate |
| `BG_DARK` | `#0d0d0d` | the dark plate |
| `MAX_OUTPUT_MB` | `5` | Google's file limit |
| `COMPOSITE_SOURCE` | `true` | paste the source artwork back after the model renders |
| `UPSTREAM_TIMEOUT` | `30` | seconds before the renderer counts as unreachable |
| `DEFAULT_MODE` | `hybrid` | `exact` \| `ai` \| `hybrid` |
| `IMAGE_MODEL` | `gpt-image-2.5-flare` | model id, change without touching code |
| `TRIM_BORDER` | `false` | keep `false` — trimming changes the logo's framing |

## Layout

```
app.py           FastAPI entrypoint
router.py        all routes (one router)
controller.py    request validation + response shaping
service.py       the five checks, the model call, validation, the composite
verify.py        pHash / SSIM / pixel / colour metrics
config.py        .env-driven settings
prompt.txt       the reproduction prompt, parameterised per logo
scripts/fetch_sheet.py         pull logo URLs out of the xlsx
scripts/fetch_sheet_images.py  pull logos embedded in the xlsx
scripts/retry_failed.py        recover SVGs and hotlink-protected sources
scripts/run_test.py            run both paths, measure everything
scripts/build_report_plain.py  results.json -> shareable HTML + CSV
scripts/run_batch.py           generic folder/URL batch through the service
```
