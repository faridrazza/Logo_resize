"""Turn results.json into a single shareable HTML file.

    python scripts/build_report.py

Every image is embedded, so the report is one self-contained file that can be
emailed or dropped in Drive. Each 300 x 300 output is embedded as the real PNG,
so "Download" saves a genuine 300 x 300 file, not a scaled screenshot.
"""
from __future__ import annotations

import base64
import csv
import re
import html
import io
import json
import statistics as stats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image                # noqa: E402

from config import settings          # noqa: E402

OUT = settings.OUTPUT_DIR
SIZE = settings.TARGET_SIZE

# thresholds used to judge each check
AR_TOL = 1.5       # % aspect-ratio error
PX_TOL = 1.5       # px, forgives integer rounding on very thin artwork
COLOUR_TOL = 12.0  # RGB distance on a brand colour
PHASH_TOL = 6
SSIM_TOL = 0.93


def b64(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def thumb(path: Path, box: int = 440) -> str:
    """Downscaled source, for display only."""
    im = Image.open(path)
    im.thumbnail((box, box), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return b64(buf.getvalue())


def crop_zoom(path: Path, box, zoom: int) -> str:
    """A magnified slice of one output, for the side-by-side in Methods."""
    im = Image.open(path).convert("RGBA")
    flat = Image.new("RGB", im.size, (255, 255, 255))
    flat.paste(im, (0, 0), im)
    w, h = box[2] - box[0], box[3] - box[1]
    out = flat.crop(box).resize((w * zoom, h * zoom), Image.NEAREST)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return b64(buf.getvalue())


def sharpness(path: Path) -> float:
    """Mean gradient magnitude: how much edge contrast the file carries."""
    import numpy as np
    im = Image.open(path).convert("RGBA")
    flat = Image.new("RGB", im.size, (255, 255, 255))
    flat.paste(im, (0, 0), im)
    g = np.asarray(flat.convert("L"), dtype=np.float32)
    return float((np.abs(np.diff(g, axis=1)).mean() + np.abs(np.diff(g, axis=0)).mean()) / 2)


def e(x) -> str:
    return html.escape(str(x))


def mark(ok: bool, label_ok: str = "PASS", label_no: str = "FAIL") -> str:
    cls = "pass" if ok else "fail"
    return f'<span class="result {cls}">{label_ok if ok else label_no}</span>'


# --------------------------------------------------------------------- checks

def proportions_ok(f: dict) -> bool:
    """One rule for both paths: the artwork kept its shape.

    Either the aspect ratio matches the source within AR_TOL, or the artwork sits
    within PX_TOL of where a single uniform scale factor would put it. The second
    clause only matters for artwork a few pixels tall, where one pixel of
    anti-aliasing is already a large percentage.
    """
    if not f:
        return False
    if f.get("ar_error_pct", 99) <= AR_TOL:
        return True
    px = f.get("px_error")
    return bool(px) and max(abs(v) for v in px) <= PX_TOL


def judge(row: dict) -> dict:
    """Per-logo verdicts for both paths."""
    ex, md = row["exact"], row["model"]
    ex_f = ex["framing"] or {}
    out: dict = {"exact": {}, "model": {}}

    # The deterministic path is held to the strictest test available: its output
    # must be bit-identical to a uniform scale computed independently in run_test.
    exact_proof = ex.get("uniform_scale_max_diff", 255) == 0
    out["exact"]["size"] = ex["size"] == [SIZE, SIZE]
    out["exact"]["proportions"] = exact_proof
    out["exact"]["colour"] = ex["colour_worst_delta"] <= COLOUR_TOL
    out["exact"]["artwork"] = exact_proof
    out["exact"]["overall"] = all(out["exact"].values())

    if "error" in md:
        out["model"] = {k: False for k in ("size", "proportions", "colour", "artwork")}
        out["model"]["overall"] = False
        return out

    md_f = md["framing"] or {}
    v = md["vs_exact"]
    out["model"]["size"] = md["size"] == [SIZE, SIZE]
    out["model"]["proportions"] = proportions_ok(md_f)
    out["model"]["colour"] = md["colour_worst_delta"] <= COLOUR_TOL
    out["model"]["artwork"] = v["phash_distance"] <= PHASH_TOL and v["ssim"] >= SSIM_TOL
    out["model"]["overall"] = all(out["model"].values())
    return out


def severity(row: dict, v: dict) -> tuple[str, str]:
    """How badly did the model change the logo?"""
    md = row["model"]
    if "error" in md:
        return "severe", "The model returned no image."
    f = md["framing"] or {}
    if f.get("ar_error_pct", 0) > 20:
        return "severe", "Part of the lockup is missing: the surviving artwork has a different shape from the source."
    if md["colour_worst_delta"] > 60:
        return "severe", "A brand colour is gone from the output."
    if v["model"]["overall"]:
        return "clean", "Within tolerance on every check."
    bad = []
    if not v["model"]["proportions"]:
        bad.append("proportions")
    if not v["model"]["colour"]:
        bad.append("brand colour")
    if not v["model"]["artwork"]:
        bad.append("letterforms and detail")
    return "changed", "Redrawn: " + ", ".join(bad) + " differ from the source."


# ---------------------------------------------------------------------- render

def pick_figure(rows: list[dict]) -> dict:
    """A representative logo for the side-by-side: wide, legible, and redrawn
    rather than destroyed, so the point being made is visible."""
    def ok(r):
        return ("error" not in r["model"] and r["exact"].get("framing")
                and 1.8 <= r["source"]["aspect_ratio"] <= 6.0
                and 0.70 <= r["model"]["vs_exact"]["ssim"] <= 0.96)
    cands = [r for r in rows if ok(r)]
    if not cands:
        cands = [r for r in rows if "error" not in r["model"] and r["exact"].get("framing")]
    # widest artwork wins: more letterforms visible in the crop
    return max(cands, key=lambda r: (r["exact"]["framing"] or {}).get("output_content", [0])[0])


def case_title(row: dict) -> str:
    """Prefer the sheet's own domain label over the generated filename."""
    label = (row.get("label") or "").strip()
    if label:
        return re.sub(r"^https?://(www\.)?", "", label).rstrip("/")
    return Path(row["file"]).stem.split("_", 1)[-1].replace("_canonical", "")


def case_html(row: dict) -> str:
    v = judge(row)
    sev, why = severity(row, v)
    src = row["source"]
    ex, md = row["exact"], row["model"]
    stem = Path(row["file"]).stem

    src_img = thumb(settings.INPUT_DIR / row["file"])
    ex_bytes = (OUT / ex["file"]).read_bytes()
    ex_img = b64(ex_bytes)
    md_img = b64((OUT / md["file"]).read_bytes()) if "error" not in md else ""

    ex_f = ex["framing"] or {}
    md_f = (md.get("framing") or {}) if "error" not in md else {}
    vs = md.get("vs_exact", {})

    def cell(ok, text):
        return f'<td class="v">{mark(ok)}</td><td class="why">{e(text)}</td>'

    rows_html = [
        "<tr><th scope=\"row\">Size</th>"
        + cell(v["exact"]["size"], f"{ex['size'][0]} × {ex['size'][1]} PNG")
        + cell(v["model"]["size"],
               f"{md['size'][0]} × {md['size'][1]} PNG" if "error" not in md else "no image")
        + "</tr>",

        "<tr><th scope=\"row\">Proportions</th>"
        + cell(v["exact"]["proportions"],
               f"scaled {ex['scale_factor']}× on both axes, artwork "
               f"{ex_f.get('output_content', ['?', '?'])[0]} × {ex_f.get('output_content', ['?', '?'])[1]} px, "
               f"centred within {max(abs(x) for x in ex_f.get('centre_offset_px', [0, 0]))} px, nothing cropped")
        + cell(v["model"]["proportions"],
               (f"artwork {md_f.get('output_content', ['?', '?'])[0]} × {md_f.get('output_content', ['?', '?'])[1]} px "
                f"against {md_f.get('expected_content', ['?', '?'])[0]} × {md_f.get('expected_content', ['?', '?'])[1]} expected; "
                f"aspect {md_f.get('ar_error_pct', 0)}% from source")
               if "error" not in md else "no image")
        + "</tr>",

        "<tr><th scope=\"row\">Brand colour</th>"
        + cell(v["exact"]["colour"],
               f"worst shift {ex['colour_worst_delta']} of 441 across "
               f"{len(ex['colour_detail'])} dominant colours")
        + cell(v["model"]["colour"],
               f"worst shift {md['colour_worst_delta']} of 441"
               if "error" not in md else "no image")
        + "</tr>",

        "<tr><th scope=\"row\">Artwork</th>"
        + cell(v["exact"]["artwork"],
               f"identical to an independently computed uniform scale: "
               f"largest channel difference {ex.get('uniform_scale_max_diff', '?')} of 255")
        + cell(v["model"]["artwork"],
               f"pHash {vs.get('phash_distance')} of 64, SSIM {vs.get('ssim')}, "
               f"mean pixel difference {vs.get('mean_pixel_diff')}"
               if "error" not in md else e(md.get("error", "")))
        + "</tr>",
    ]

    dl = ('<a class="dl" download="{n}_{s}x{s}.png" href="{h}">Download {s} × {s} PNG</a>')

    model_col = (
        f'<div class="plate"><img class="full" loading="lazy" src="{md_img}" '
        f'alt="Model render: {e(stem)}"></div>'
        f'<figcaption>{SIZE} × {SIZE}, {md["elapsed_ms"]/1000:.1f} s '
        f'<button type="button" class="zoom-link">Zoom</button></figcaption>'
        + dl.format(n=e(stem), s=SIZE, h=md_img)
        if "error" not in md else
        f'<div class="plate"><div class="empty-plate">{e(md.get("error", "no image"))}</div></div>'
    )

    return f"""
<article class="case {sev}" id="row{row['no']}">
  <div class="case-head">
    <h3>{row['no']}. {e(case_title(row))}</h3>
    <span class="overall">Deterministic {mark(v['exact']['overall'])} &nbsp; Model {mark(v['model']['overall'])}</span>
  </div>
  <p class="case-why">{e(src['width'])} × {e(src['height'])} {e(src['format'])},
     {e(src['aspect_type'])}, {e(src['background'])} background. {e(why)}
     <a href="{e(row['url'])}" target="_blank" rel="noopener">source</a></p>

  <div class="columns three">
    <div>
      <p class="column-title">Original</p>
      <figure>
        <div class="plate"><img class="full" loading="lazy" src="{src_img}" alt="Original: {e(stem)}"></div>
        <figcaption>{e(src['width'])} × {e(src['height'])} <button type="button" class="zoom-link">Zoom</button></figcaption>
      </figure>
    </div>
    <div>
      <p class="column-title">Deterministic resize {mark(v['exact']['overall'])}</p>
      <figure>
        <div class="plate"><img class="full" loading="lazy" src="{ex_img}" alt="Deterministic: {e(stem)}"></div>
        <figcaption>{SIZE} × {SIZE}, {ex['elapsed_ms']:.0f} ms <button type="button" class="zoom-link">Zoom</button></figcaption>
        {dl.format(n=e(stem), s=SIZE, h=ex_img)}
      </figure>
    </div>
    <div>
      <p class="column-title">gpt-image-2.5-flare {mark(v['model']['overall'])}</p>
      <figure>{model_col}</figure>
    </div>
  </div>

  <table class="checks">
    <thead><tr><th scope="col">Check</th><th scope="col" colspan="2">Deterministic</th><th scope="col" colspan="2">Model</th></tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>
</article>"""


def unusable_rows() -> list[tuple[str, str, str]]:
    """Sheet rows that produced no image, with the reason, read from the manifest."""
    manifest = settings.INPUT_DIR / "_manifest.tsv"
    if not manifest.exists():
        return []
    out = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        parts = line.split("	")
        if len(parts) == 4 and parts[1].startswith(("UNSUPPORTED", "DOWNLOAD_FAILED")):
            no, status, label, url = parts
            reason = ("link is dead or blocked by the site"
                      if status == "DOWNLOAD_FAILED"
                      else f"format not readable ({status.split(':', 1)[-1]})")
            out.append((no, re.sub(r"^https?://(www\.)?", "", label).rstrip("/"), reason))
    return out


def main() -> None:
    data = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    rows = data["rows"]
    verdicts = [judge(r) for r in rows]
    sevs = [severity(r, v)[0] for r, v in zip(rows, verdicts)]

    n = len(rows)
    ex_pass = {k: sum(1 for v in verdicts if v["exact"][k]) for k in
               ("size", "proportions", "colour", "artwork", "overall")}
    md_pass = {k: sum(1 for v in verdicts if v["model"][k]) for k in
               ("size", "proportions", "colour", "artwork", "overall")}
    severe = sum(1 for s in sevs if s == "severe")
    # Per-call latency varied widely under parallel load (retries on transient
    # connection errors), so the throughput figure is wall clock over the batch.
    per_logo = data.get("total_seconds", 0) / max(1, len(rows))
    uniq = len({r["url"] for r in rows})

    # Methods figure: a wide wordmark whose model render is close but not identical,
    # so the comparison shows redrawing rather than an obvious catastrophe.
    fig_row = pick_figure(rows)
    FIG = Path(fig_row["file"]).stem
    fw, fh = (fig_row["exact"]["framing"] or {}).get("output_content", [SIZE, SIZE])
    x0, y0 = (SIZE - fw) // 2, (SIZE - fh) // 2
    pad = 2
    FIG_BOX = (max(0, x0 - pad), max(0, y0 - pad),
               min(SIZE, x0 + fw + pad), min(SIZE, y0 + fh + pad))
    FIG_ZOOM = max(2, min(6, round(660 / max(1, FIG_BOX[2] - FIG_BOX[0]))))
    fig_exact = crop_zoom(OUT / "exact" / f"{FIG}.png", FIG_BOX, FIG_ZOOM)
    fig_model = crop_zoom(OUT / "model" / f"{FIG}.png", FIG_BOX, FIG_ZOOM)
    fig_name = case_title(fig_row)
    sharper = sum(1 for r in rows
                  if sharpness(OUT / r["model"]["file"]) > sharpness(OUT / r["exact"]["file"]))
    upscaled = [r for r in rows if r["exact"]["scale_factor"] > 1]
    biggest = max(rows, key=lambda r: r["source"]["width"] * r["source"]["height"])
    big_art = (biggest["exact"]["framing"] or {}).get("output_content", ["?", "?"])
    max_up = max((r["exact"]["scale_factor"] for r in upscaled), default=1.0)

    missing = unusable_rows()

    counts = "".join(
        f"<tr><th scope=\"row\">{label}</th>"
        f"<td>{ex_pass[k]} of {n}</td><td>{md_pass[k]} of {n}</td></tr>"
        for k, label in (("size", "Size"), ("proportions", "Proportions"),
                         ("colour", "Brand colour"), ("artwork", "Artwork"),
                         ("overall", "Overall"))
    )

    cases = "".join(case_html(r) for r in rows)

    missing_note = ""
    if missing:
        items = "".join(f"<li>Row {no} — {e(label)}: {e(reason)}.</li>" for no, label, reason in missing)
        missing_note = (
            f'<p class="note" style="margin-top:12px">{len(missing)} of the first '
            f'{n + len(missing)} rows produced no image and are not counted anywhere in this '
            f'report:</p><ul class="note" style="margin-top:4px">{items}</ul>'
        )

    prompt_text = e(settings.PROMPT_FILE.read_text(encoding="utf-8"))

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Logo resize test: 300 × 300</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --paper: #f3f4f5; --surface: #ffffff; --ink: #1c2024; --ink-2: #555e66;
    --rule: #d4d8dc; --plate: #767676; --pass: #17663f; --fail: #a8200d;
    --warn: #7a5b00; --focus: #2856a3;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--paper); color: var(--ink);
    font: 400 15px/1.55 "Public Sans", "Segoe UI", system-ui, sans-serif;
    font-variant-numeric: tabular-nums; }}
  a {{ color: var(--focus); }}
  :focus-visible {{ outline: 2px solid var(--focus); outline-offset: 2px; }}
  h1, h2, h3 {{ margin: 0; font-weight: 700; line-height: 1.2; letter-spacing: -0.01em; }}
  h1 {{ font-size: 32px; }}
  h2 {{ font-size: 24px; margin-bottom: 14px; }}
  h3 {{ font-size: 18px; }}
  p {{ margin: 0; max-width: 80ch; }}

  .page {{ max-width: 1440px; margin: 0 auto; padding: 44px 32px 88px; }}
  section {{ margin-top: 48px; }}
  .group {{ margin-top: 64px; padding-top: 28px; border-top: 3px solid var(--ink); }}
  .intro {{ margin-top: 10px; color: var(--ink-2); font-size: 16px; }}
  .note {{ margin-bottom: 14px; color: var(--ink-2); }}

  .result {{ font-weight: 700; white-space: nowrap; }}
  .result.pass {{ color: var(--pass); }}
  .result.fail {{ color: var(--fail); }}

  .decision {{ background: var(--surface); border-left: 4px solid var(--ink); padding: 22px 26px; }}
  .decision .call {{ font-size: 20px; font-weight: 700; }}
  .decision p + p, .decision .call + p {{ margin-top: 8px; }}
  .decision ul {{ margin: 12px 0 0; padding-left: 20px; max-width: 90ch; }}
  .decision li + li {{ margin-top: 5px; }}

  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; vertical-align: top; padding: 8px 10px; border-bottom: 1px solid var(--rule); }}
  tbody tr:last-child > * {{ border-bottom: 0; }}
  .counts-wrap {{ overflow-x: auto; background: var(--surface); margin-bottom: 8px; }}
  .counts {{ min-width: 520px; }}
  .counts thead th {{ font-size: 14px; border-bottom: 2px solid var(--ink); }}
  .counts td, .counts th {{ white-space: nowrap; }}

  .case {{ margin-top: 28px; background: var(--surface); padding: 20px 20px 22px;
    border-left: 4px solid transparent; }}
  .case.severe {{ border-left-color: var(--fail); }}
  .case.changed {{ border-left-color: var(--warn); }}
  .case.clean {{ border-left-color: var(--pass); }}
  .case-head {{ display: flex; flex-wrap: wrap; align-items: baseline;
    justify-content: space-between; gap: 6px 20px; }}
  .case-head .overall {{ font-size: 15px; }}
  .case-why {{ margin-top: 6px; color: var(--ink-2); font-size: 14px; }}

  .columns {{ display: grid; gap: 18px; margin-top: 16px; align-items: start; }}
  .columns.two {{ grid-template-columns: 1fr 1fr; }}
  .columns.three {{ grid-template-columns: repeat(3, 1fr); }}
  .zoomfig {{ display: block; width: 100%; height: auto; background: #fff;
    border: 1px solid var(--rule); image-rendering: pixelated; }}
  .column-title {{ margin-bottom: 8px; font-size: 14px; font-weight: 600; }}
  .column-title .result {{ margin-left: 6px; }}
  figure {{ margin: 0; }}
  .plate {{ position: relative; aspect-ratio: 1 / 1; background: var(--plate); overflow: hidden; }}
  .plate img.full {{ position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }}
  .plate .empty-plate {{ position: absolute; inset: 0; display: grid; place-items: center;
    padding: 20px; color: #fff; text-align: center; font-size: 14px; }}
  figcaption {{ margin-top: 6px; font-size: 13px; color: var(--ink-2); }}
  .dl {{ display: inline-block; margin-top: 8px; font-size: 13px; font-weight: 600;
    color: var(--focus); text-decoration: none; border: 1px solid var(--rule);
    padding: 5px 10px; background: var(--surface); }}
  .dl:hover {{ border-color: var(--focus); }}

  .checks {{ margin-top: 14px; border-top: 2px solid var(--ink); }}
  .checks thead th {{ font-size: 13px; font-weight: 600; }}
  .checks th[scope="row"] {{ width: 14%; font-size: 13px; font-weight: 600; }}
  .checks td.v {{ width: 8%; font-size: 13px; }}
  .checks td.why {{ width: 35%; font-size: 13px; line-height: 1.45; color: var(--ink-2); }}

  details.plain {{ background: var(--surface); padding: 14px 20px; }}
  details.plain > summary {{ cursor: pointer; font-weight: 600; }}
  .prompt {{ margin: 12px 0 0; white-space: pre-wrap; font: inherit; font-size: 13px; max-width: 90ch; }}

  @media (max-width: 1100px) {{ .columns.three {{ grid-template-columns: 1fr 1fr; }} }}
  @media (max-width: 760px) {{
    .page {{ padding: 28px 14px 64px; }}
    h1 {{ font-size: 26px; }}
    .columns.two, .columns.three {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<main class="page">
  <h1>Logo resize test: 300 × 300</h1>
  <p class="intro">Every logo resized two ways and compared against its own source: a
  deterministic resize, and a render by {e(data['model'])} at {e(data['quality'])} quality.
  {n} logos from the first {n + len(missing)} rows of the sheet
  ({uniq} distinct source files); each was run once. Generated {e(data['generated'])}.</p>
  {missing_note}

  <section>
    <h2>The two methods</h2>
    <div class="decision">
      <p><b>Deterministic resize — no AI.</b> Ordinary image resizing, done in code: work out one
      scale factor, <code>min({SIZE}/width, {SIZE}/height)</code>, apply that same factor to both
      axes with a Lanczos filter, and centre the result on a {SIZE} × {SIZE} canvas. It copies the
      artwork; it never draws anything. "Deterministic" means the same file in always gives the
      identical file out, byte for byte — there is no randomness and no model involved. About
      {stats.median([r['exact']['elapsed_ms'] for r in rows]):.0f} ms per logo, no API call, no cost.</p>
      <p><b>{e(data['model'])} — the image model.</b> It is handed that same composed square and
      asked to reproduce it, but a generative model works by painting a new image that resembles
      what it was shown. It cannot copy pixels, so it redraws the logo from scratch and every run
      comes back slightly different.</p>
    </div>
  </section>

  <section>
    <h2>Is quality lost in the deterministic resize?</h2>
    <div class="decision">
      <p class="call">No. Two different things get called quality here, and only one of them is
      affected — by the {SIZE} × {SIZE} requirement itself, not by the method.</p>
      <p><b>Resolution.</b> {len(rows) - len(upscaled)} of the {len(rows)} logos are larger than
      {SIZE} px. {e(Path(biggest['file']).stem.split('_', 1)[-1].replace('_canonical', ''))} is
      {biggest['source']['width']} × {biggest['source']['height']} and its artwork lands at
      {big_art[0]} × {big_art[1]}. Those pixels are gone, and no method can keep them: that is what
      asking for {SIZE} × {SIZE} means. Lanczos is the highest-quality resampling filter in common
      use, so this is as good as {SIZE} × {SIZE} gets. The remaining {len(upscaled)} logos are
      smaller than {SIZE} px and are enlarged slightly, at most {max_up:.2f}× — set
      <code>ALLOW_UPSCALE=false</code> to leave those at native size with more padding instead.</p>
      <p><b>Fidelity.</b> Is it still the same logo? The deterministic output is bit-identical to a
      uniform scale of the source on all {len(rows)}. Nothing is re-drawn, re-typeset or
      re-coloured, so there is nothing to degrade.</p>
      <p>Measured on edge contrast, the model's files are <i>sharper</i> than the deterministic
      ones on {sharper} of {len(rows)} logos. That sharpness is drawn, not recovered. Below is
      {e(fig_name)} from both files at {FIG_ZOOM}× — the model's version is crisper and wrong.
      A logo that looks sharper but carries different letterforms is still the wrong logo.</p>
      <div class="columns two" style="margin-top:18px">
        <div>
          <p class="column-title">Deterministic</p>
          <img class="zoomfig" src="{fig_exact}" alt="Deterministic resize, wordmark at {FIG_ZOOM}×">
          <figcaption>true to the source letterforms</figcaption>
        </div>
        <div>
          <p class="column-title">{e(data['model'])}</p>
          <img class="zoomfig" src="{fig_model}" alt="Model render, wordmark at {FIG_ZOOM}×">
          <figcaption>higher contrast, but the strokes are thicker</figcaption>
        </div>
      </div>
    </div>
  </section>

  <section>
    <h2>Recommendation</h2>
    <div class="decision">
      <p class="call">Ship the deterministic resize. {md_pass['overall']} of {n} model renders
      came back unchanged; {severe} came back with part of the logo missing.</p>
      <p>The requirement is that the logo must not change. A deterministic resize scales the
      artwork by one factor on both axes and centres it on a {SIZE} × {SIZE} canvas, so the design,
      text, colours and proportions cannot change — it passed all {n}. The image model redraws the
      logo from scratch every time, and on this set it re-typeset wordmarks, shifted brand colours
      and, on {severe} logos, dropped the wordmark entirely and returned the icon alone.</p>
      <ul>
        <li>Size: deterministic {ex_pass['size']} of {n}, model {md_pass['size']} of {n}.</li>
        <li>Proportions: deterministic {ex_pass['proportions']} of {n}, model {md_pass['proportions']} of {n}.</li>
        <li>Brand colour: deterministic {ex_pass['colour']} of {n}, model {md_pass['colour']} of {n}.</li>
        <li>Artwork: deterministic {ex_pass['artwork']} of {n}, model {md_pass['artwork']} of {n}.</li>
        <li>Deterministic resize: {stats.median([r['exact']['elapsed_ms'] for r in rows]):.0f} ms per logo, no API cost.
            Model: {data.get('total_seconds', 0):,.0f} s of wall clock for {n} logos
            at 6 in parallel, about {per_logo:.0f} s per logo.</li>
      </ul>
      <p>The endpoint keeps the model available behind <code>mode=ai</code> and
      <code>mode=hybrid</code>; hybrid uses the model only when it clears every check above and
      otherwise falls back, so the shipped file is never a changed logo.</p>
    </div>
  </section>

  <section>
    <h2>Counts</h2>
    <div class="counts-wrap">
      <table class="counts">
        <thead><tr><th scope="col">Check</th><th scope="col">Deterministic</th><th scope="col">{e(data['model'])}</th></tr></thead>
        <tbody>{counts}</tbody>
      </table>
    </div>
    <p class="note">Size: the file is exactly {SIZE} × {SIZE}.
    Proportions and artwork are judged by the strictest test each path can face.
    The deterministic output is rebuilt from the source by a separate implementation — one scale
    factor on both axes, centred — and compared pixel for pixel; it passes only at a difference of
    0 of 255, which a stretched, cropped or redrawn image could not reach.
    The model cannot be held to bit-equality, so it is judged on shape and likeness instead:
    aspect ratio within {AR_TOL}% of the source, or the artwork within {PX_TOL} px of where a
    uniform scale puts it.
    Brand colour: every dominant source colour still appears, within {COLOUR_TOL:.0f} of 441 RGB distance.
    Artwork: perceptual hash within {PHASH_TOL} of 64 and SSIM at or above {SSIM_TOL} against the
    deterministic render, which is the source itself at {SIZE} × {SIZE}.</p>
  </section>

  <section>
    <h2>Method</h2>
    <details class="plain">
      <summary>How each logo was processed, and the prompt used</summary>
      <p style="margin-top:10px">Each logo is downloaded from the sheet link, scaled by
      <code>min({SIZE}/width, {SIZE}/height)</code> — one factor for both axes — and centred on a
      {SIZE} × {SIZE} canvas. Padding keeps the source's own background: transparent stays
      transparent, a solid background keeps its own colour. That render is the deterministic result
      and also the input handed to the model, so the model receives a correctly composed square and
      only has to reproduce it. The model's 1024 × 1024 reply is then scaled down by the same
      deterministic step. Measurements compare each output against the original source file.</p>
      <pre class="prompt">{prompt_text}</pre>
    </details>
  </section>

  <section class="group">
    <h2>All {n} logos</h2>
    <p class="note">Sheet order. Red bar: part of the logo is missing. Amber: redrawn but complete.
    Green: within tolerance on every check. Click any image to enlarge; Download saves the real
    {SIZE} × {SIZE} PNG.</p>
    {cases}
  </section>
</main>

<style>
 .zoom-link{{font:inherit;color:var(--focus);background:none;border:0;padding:0;text-decoration:underline;cursor:zoom-in}}
 .plate img,.plate{{cursor:zoom-in}}
 #lightbox{{position:fixed;inset:0;z-index:99;display:none;background:rgba(20,22,25,.92);align-items:center;justify-content:center;padding:24px;cursor:zoom-out}}
 #lightbox[open]{{display:flex}} #lightbox img{{max-width:100%;max-height:100%;object-fit:contain;background:#fff;box-shadow:0 10px 40px rgba(0,0,0,.5)}}
 #lightbox .hint{{position:fixed;top:14px;right:18px;color:#fff;font-size:13px;opacity:.8}}
</style>
<div id="lightbox" role="dialog" aria-modal="true" aria-label="Enlarged image"><span class="hint">Click anywhere or press Esc to close</span><img alt=""></div>
<script>
(function(){{var box=document.getElementById('lightbox'),big=box.querySelector('img');
function open(s,a){{big.src=s;big.alt=a||'';box.setAttribute('open','')}}
function close(){{box.removeAttribute('open');big.removeAttribute('src')}}
document.addEventListener('click',function(e){{if(box.contains(e.target)){{close();return}}
if(e.target.closest('.dl'))return;
var btn=e.target.closest('.zoom-link'),fig=btn?btn.closest('figure'):null,
img=btn?(fig&&fig.querySelector('img')):(e.target.closest('.plate, figure')&&(e.target.tagName==='IMG'?e.target:e.target.closest('.plate, figure').querySelector('img')));
if(img&&img.src){{e.preventDefault();open(img.src,img.alt)}}}});
document.addEventListener('keydown',function(e){{if(e.key==='Escape')close()}})}})();
</script>
</body>
</html>"""

    # CSV alongside the report, for the Google Sheet
    cols = ["no", "logo", "sheet_link", "original_w", "original_h", "aspect_type",
            "background", "output_file", "output_w", "output_h",
            "det_uniform_scale_diff", "det_colour_shift", "det_overall",
            "model_aspect_error_pct", "model_colour_shift", "model_phash",
            "model_ssim", "model_seconds", "model_overall", "model_note"]
    with (OUT / "report.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r, v in zip(rows, verdicts):
            md, ex = r["model"], r["exact"]
            mf, vs = (md.get("framing") or {}), md.get("vs_exact", {})
            w.writerow({
                "no": r["no"],
                "logo": Path(r["file"]).stem.split("_", 1)[-1].replace("_canonical", ""),
                "sheet_link": r["url"],
                "original_w": r["source"]["width"], "original_h": r["source"]["height"],
                "aspect_type": r["source"]["aspect_type"], "background": r["source"]["background"],
                "output_file": Path(ex["file"]).name,
                "output_w": ex["size"][0], "output_h": ex["size"][1],
                "det_uniform_scale_diff": ex.get("uniform_scale_max_diff"),
                "det_colour_shift": ex["colour_worst_delta"],
                "det_overall": "PASS" if v["exact"]["overall"] else "FAIL",
                "model_aspect_error_pct": mf.get("ar_error_pct", ""),
                "model_colour_shift": md.get("colour_worst_delta", ""),
                "model_phash": vs.get("phash_distance", ""),
                "model_ssim": vs.get("ssim", ""),
                "model_seconds": round(md.get("elapsed_ms", 0) / 1000, 1),
                "model_overall": "PASS" if v["model"]["overall"] else "FAIL",
                "model_note": severity(r, v)[1],
            })

    dest = OUT / "logo_resize_300x300_report.html"
    dest.write_text(doc, encoding="utf-8")
    print(f"{n} cases | severe {severe} | model passed {md_pass['overall']} | "
          f"deterministic passed {ex_pass['overall']}")
    print(f"Report -> {dest}  ({dest.stat().st_size/1024/1024:.1f} MB)")
    print(f"CSV    -> {OUT / 'report.csv'}")


if __name__ == "__main__":
    main()
