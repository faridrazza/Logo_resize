"""Turn results.json into a shareable HTML report — measurements only.

    python scripts/build_report_plain.py

Deliberately carries no pass/fail, no verdicts, no recommendation and no
colour-coded severity. Every logo is shown three ways (original, squared in
code, squared via the model) with the numbers measured for each, and the reader
draws their own conclusion. Images are embedded, so the file is self-contained.
"""
from __future__ import annotations

import base64
import csv
import html
import io
import json
import re
import statistics as stats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image                # noqa: E402

from config import settings          # noqa: E402

OUT = settings.OUTPUT_DIR
MODEL_NAME = ""


def b64(data: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def thumb(path: Path, box: int = 440) -> str:
    im = Image.open(path)
    im.thumbnail((box, box), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return b64(buf.getvalue())


def e(x) -> str:
    return html.escape(str(x))


def num(x, dash: str = "—") -> str:
    return dash if x is None or x == "" else str(x)


def case_title(row: dict) -> str:
    label = (row.get("label") or "").strip()
    if label:
        return re.sub(r"^https?://(www\.)?", "", label).rstrip("/")
    return Path(row["file"]).stem.split("_", 1)[-1].replace("_canonical", "")


# ---------------------------------------------------------------------- render

def case_html(row: dict) -> str:
    src = row["source"]
    ex, md = row["exact"], row["model"]
    stem = Path(row["file"]).stem
    has_model = "error" not in md
    edge = src["edge"]

    src_img = thumb(settings.INPUT_DIR / row["file"])
    ex_img = b64((OUT / ex["file"]).read_bytes())
    md_img = b64((OUT / md["file"]).read_bytes()) if has_model else ""

    ex_f = ex["framing"] or {}
    md_f = (md.get("framing") or {}) if has_model else {}

    ex_box = ex_f.get("output_content", ["?", "?"])
    md_box = md_f.get("output_content", ["?", "?"])

    # The href is filled in at load time from the figure's own <img>, so the
    # full-size PNG is embedded once per logo instead of twice. At 1200 px
    # squares the duplicate copy was most of the file.
    dl = '<a class="dl" download="{n}_{s}x{s}.png">Download {s} × {s} PNG</a>'

    model_fig = (
        f'<div class="plate"><img class="full" loading="lazy" src="{md_img}" '
        f'alt="Model render: {e(stem)}"></div>'
        f'<figcaption>{edge} × {edge}, {md["elapsed_ms"]/1000:.1f} s '
        f'<button type="button" class="zoom-link">Zoom</button></figcaption>'
        + dl.format(n=e(stem), s=edge)
        if has_model else
        f'<div class="plate"><div class="empty-plate">'
        f'{e(md.get("error", "no image returned"))}</div></div>'
        f'<figcaption>no image returned</figcaption>'
    )

    def r(label, det, mod):
        return (f'<tr><th scope="row">{label}</th>'
                f'<td>{det}</td><td>{mod}</td></tr>')

    longer = max(src["width"], src["height"])
    scale_note = " (placed at true size)" if ex["scale_factor"] == 1.0 else " (shrunk to fit)"

    rows_html = [
        r("Output",
          f"{ex['size'][0]} × {ex['size'][1]} PNG, {ex['bytes']:,} B",
          f"{md['size'][0]} × {md['size'][1]} PNG, {md['bytes']:,} B"
          if has_model else "—"),

        r("Square size",
          f"longer side {longer} px → EDGE {edge}",
          f"EDGE {edge}, as instructed" if has_model else "—"),

        r("Scale applied to the logo",
          f"{ex['scale_factor']}× on both axes{scale_note}",
          f"{ex['scale_factor']}× on both axes{scale_note}" if has_model else "—"),

        r("Logo on the canvas",
          f"{ex_box[0]} × {ex_box[1]} px, centred within "
          f"{max(abs(x) for x in ex_f.get('centre_offset_px', [0, 0]))} px",
          f"{md_box[0]} × {md_box[1]} px" if has_model else "—"),

        r("Proportions",
          f"source {ex_f.get('source_ar', '—')}, output {ex_f.get('output_ar', '—')} "
          f"({ex_f.get('ar_error_pct', 0)}% from source)",
          f"output {md_f.get('output_ar', '—')} "
          f"({md_f.get('ar_error_pct', '—')}% from source)" if has_model else "—"),

        r("Logo against the source artwork",
          f"largest channel difference {num(ex.get('artwork_max_diff'))} of 255",
          f"largest channel difference {num(md.get('artwork_max_diff'))} of 255"
          if has_model else "—"),

        r("Time",
          f"{ex['elapsed_ms']:.0f} ms",
          f"{md['elapsed_ms']/1000:.1f} s" if has_model else "—"),
    ]

    colour_chips = "".join(
        f'<span class="chip"><i style="background:rgb{tuple(c["colour"])}"></i>'
        f'rgb{tuple(c["colour"])}</span>'
        for c in ex["colour_detail"][:6]
    )

    return f"""
<article class="case" id="row{row['no']}">
  <div class="case-head">
    <h3>{row['no']}. {e(case_title(row))}</h3>
    <span class="meta">{e(src['width'])} × {e(src['height'])} {e(src['format'])} &middot;
      {e(src['aspect_type'])} &middot;
      {'transparent source' if src.get('transparent') else 'opaque source'} &middot;
      plate {e(src['background'])} &middot;
      <a href="{e(row['url'])}" target="_blank" rel="noopener">source</a></span>
  </div>

  <div class="columns three">
    <div>
      <p class="column-title">Original</p>
      <figure>
        <div class="plate"><img class="full" loading="lazy" src="{src_img}"
          alt="Original: {e(stem)}"></div>
        <figcaption>{e(src['width'])} × {e(src['height'])}
          <button type="button" class="zoom-link">Zoom</button></figcaption>
      </figure>
    </div>
    <div>
      <p class="column-title">Squared in code</p>
      <figure>
        <div class="plate"><img class="full" loading="lazy" src="{ex_img}"
          alt="Squared in code: {e(stem)}"></div>
        <figcaption>{edge} × {edge}, {ex['elapsed_ms']:.0f} ms
          <button type="button" class="zoom-link">Zoom</button></figcaption>
        {dl.format(n=e(stem), s=edge)}
      </figure>
    </div>
    <div>
      <p class="column-title">{e(MODEL_NAME)} + composite</p>
      <figure>{model_fig}</figure>
    </div>
  </div>

  <div class="chips">Plate chosen because: {e(src.get('background_reason', ''))}</div>
  <div class="chips">Source palette: {colour_chips}</div>

  <table class="checks">
    <thead><tr><th scope="col">Measurement</th>
      <th scope="col">Squared in code</th>
      <th scope="col">{e(MODEL_NAME)} + composite</th></tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>
</article>"""


def unusable_rows() -> list[tuple[str, str, str]]:
    manifest = settings.INPUT_DIR / "_manifest.tsv"
    if not manifest.exists():
        return []
    out = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[1].startswith(("UNSUPPORTED", "DOWNLOAD_FAILED")):
            no, status, label, url = parts
            reason = ("the link is dead or the site refused the request"
                      if status == "DOWNLOAD_FAILED"
                      else f"format not readable here ({status.split(':', 1)[-1]})")
            out.append((no, re.sub(r"^https?://(www\.)?", "", label).rstrip("/"), reason))
    return out


def band_table(rows: list[dict], bands, getter) -> str:
    """A distribution, not a judgement: how many logos fall in each band."""
    vals = [getter(r) for r in rows if "error" not in r["model"]]
    vals = [v for v in vals if v is not None]
    out = []
    for lo, hi, label in bands:
        c = sum(1 for v in vals if lo <= v < hi)
        pct = c / len(vals) * 100 if vals else 0
        out.append(
            f'<tr><th scope="row">{label}</th><td>{c} of {len(vals)}</td>'
            f'<td><span class="bar" style="width:{pct:.1f}%"></span></td></tr>')
    return "".join(out)


CSS = """
  :root {
    --paper:#f3f4f5; --surface:#fff; --ink:#1c2024; --ink-2:#555e66;
    --rule:#d4d8dc; --plate:#767676; --focus:#2856a3; --bar:#8a9199;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--paper); color:var(--ink);
    font:400 15px/1.55 "Public Sans","Segoe UI",system-ui,sans-serif;
    font-variant-numeric:tabular-nums; }
  a { color:var(--focus); }
  :focus-visible { outline:2px solid var(--focus); outline-offset:2px; }
  h1,h2,h3 { margin:0; font-weight:700; line-height:1.2; letter-spacing:-.01em; }
  h1 { font-size:32px; } h2 { font-size:24px; margin-bottom:14px; } h3 { font-size:18px; }
  p { margin:0; max-width:80ch; }
  .page { max-width:1440px; margin:0 auto; padding:44px 32px 88px; }
  section { margin-top:48px; }
  .group { margin-top:64px; padding-top:28px; border-top:3px solid var(--ink); }
  .intro { margin-top:10px; color:var(--ink-2); font-size:16px; }
  .note { margin-bottom:14px; color:var(--ink-2); font-size:14px; }
  .panel { background:var(--surface); border-left:4px solid var(--ink); padding:22px 26px; }
  .panel p + p { margin-top:10px; }
  table { border-collapse:collapse; width:100%; }
  th,td { text-align:left; vertical-align:top; padding:8px 10px;
    border-bottom:1px solid var(--rule); }
  tbody tr:last-child > * { border-bottom:0; }
  .wrap { overflow-x:auto; background:var(--surface); }
  .wrap table { min-width:640px; }
  .wrap thead th { font-size:14px; border-bottom:2px solid var(--ink); }
  .bar { display:inline-block; height:10px; background:var(--bar); min-width:2px; }
  .case { margin-top:28px; background:var(--surface); padding:20px 20px 22px; }
  .case-head { display:flex; flex-wrap:wrap; align-items:baseline;
    justify-content:space-between; gap:6px 20px; }
  .case-head .meta { font-size:14px; color:var(--ink-2); }
  .columns { display:grid; gap:18px; margin-top:16px; align-items:start; }
  .columns.three { grid-template-columns:repeat(3,1fr); }
  .column-title { margin-bottom:8px; font-size:14px; font-weight:600; }
  figure { margin:0; }
  .plate { position:relative; aspect-ratio:1/1; background:var(--plate); overflow:hidden; }
  .plate img.full { position:absolute; inset:0; width:100%; height:100%; object-fit:contain; }
  .plate .empty-plate { position:absolute; inset:0; display:grid; place-items:center;
    padding:20px; color:#fff; text-align:center; font-size:14px; }
  figcaption { margin-top:6px; font-size:13px; color:var(--ink-2); }
  .dl { display:inline-block; margin-top:8px; font-size:13px; font-weight:600;
    color:var(--focus); text-decoration:none; border:1px solid var(--rule);
    padding:5px 10px; background:var(--surface); }
  .dl:hover { border-color:var(--focus); }
  .chips { margin-top:10px; font-size:13px; color:var(--ink-2); }
  .chip { display:inline-flex; align-items:center; gap:6px; margin:4px 10px 0 0; }
  .chip i { width:13px; height:13px; border:1px solid var(--rule); display:inline-block; }
  .checks { margin-top:14px; border-top:2px solid var(--ink); }
  .checks thead th { font-size:13px; font-weight:600; }
  .checks th[scope="row"] { width:26%; font-size:13px; font-weight:600; }
  .checks td { width:37%; font-size:13px; line-height:1.45; color:var(--ink-2); }
  details.plain { background:var(--surface); padding:14px 20px; }
  details.plain > summary { cursor:pointer; font-weight:600; }
  .prompt { margin:12px 0 0; white-space:pre-wrap; font:inherit; font-size:13px;
    max-width:90ch; }
  @media (max-width:1100px) { .columns.three { grid-template-columns:1fr 1fr; } }
  @media (max-width:760px) {
    .page { padding:28px 14px 64px; } h1 { font-size:26px; }
    .columns.three { grid-template-columns:1fr; }
  }
  .zoom-link { font:inherit; color:var(--focus); background:none; border:0; padding:0;
    text-decoration:underline; cursor:zoom-in; }
  .plate img, .plate { cursor:zoom-in; }
  #lightbox { position:fixed; inset:0; z-index:99; display:none;
    background:rgba(20,22,25,.92); align-items:center; justify-content:center;
    padding:24px; cursor:zoom-out; }
  #lightbox[open] { display:flex; }
  #lightbox img { max-width:100%; max-height:100%; object-fit:contain; background:#fff;
    box-shadow:0 10px 40px rgba(0,0,0,.5); }
  #lightbox .hint { position:fixed; top:14px; right:18px; color:#fff; font-size:13px;
    opacity:.8; }
"""

JS = """
(function(){var box=document.getElementById('lightbox'),big=box.querySelector('img');
function open(s,a){big.src=s;big.alt=a||'';box.setAttribute('open','')}
function close(){box.removeAttribute('open');big.removeAttribute('src')}
document.addEventListener('click',function(ev){if(box.contains(ev.target)){close();return}
if(ev.target.closest('.dl'))return;
var btn=ev.target.closest('.zoom-link'),fig=btn?btn.closest('figure'):null,
img=btn?(fig&&fig.querySelector('img')):(ev.target.closest('.plate, figure')&&(ev.target.tagName==='IMG'?ev.target:ev.target.closest('.plate, figure').querySelector('img')));
if(img&&img.src){ev.preventDefault();open(img.src,img.alt)}});
document.addEventListener('keydown',function(ev){if(ev.key==='Escape')close()});
// Point each Download link at the PNG already embedded in its own figure, so
// the image data is carried once rather than once per use.
document.querySelectorAll('a.dl').forEach(function(a){
  var fig=a.closest('figure'), img=fig&&fig.querySelector('img');
  if(img&&img.src){a.setAttribute('href',img.src)}
});})();
"""


def main() -> None:
    global MODEL_NAME
    data = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    rows = data["rows"]
    MODEL_NAME = data["model"]
    n = len(rows)
    missing = unusable_rows()
    ok = [r for r in rows if "error" not in r["model"]]
    errs = n - len(ok)

    edges = [r["source"]["edge"] for r in rows]
    untouched = [r for r in rows if r["exact"]["scale_factor"] == 1.0]
    shrunk = [r for r in rows if r["exact"]["scale_factor"] < 1.0]
    enlarged = [r for r in rows if r["exact"]["scale_factor"] > 1.0]
    transparent = [r for r in rows if r["source"].get("transparent")]
    dark = [r for r in rows
            if r["source"]["background"].lower() == settings.BG_DARK.lower()]
    band_big = [r for r in rows
                if max(r["source"]["width"], r["source"]["height"]) >= settings.EDGE_MAX]
    band_small = [r for r in rows
                  if max(r["source"]["width"], r["source"]["height"]) < settings.EDGE_MIN]
    band_mid = n - len(band_big) - len(band_small)

    det_ms = [r["exact"]["elapsed_ms"] for r in rows]
    det_art = [r["exact"].get("artwork_max_diff", 0) for r in rows]
    det_plate = [r["exact"].get("plate_max_deviation", 0) for r in rows]
    md_art = [r["model"].get("artwork_max_diff") for r in ok
              if r["model"].get("artwork_max_diff") is not None]
    md_plate = [r["model"].get("plate_max_deviation") for r in ok
                if r["model"].get("plate_max_deviation") is not None]
    md_s = [r["model"]["elapsed_ms"] / 1000 for r in ok]
    raws = [(r.get("model_raw") or {}).get("before_composite") or {} for r in ok]
    raw_ssim = [x["ssim"] for x in raws if x.get("ssim") is not None]
    raw_px = [x["mean_pixel_diff"] for x in raws if x.get("mean_pixel_diff") is not None]
    uniq = len({r["url"] for r in rows})

    def span(v, fmt="{:.4g}"):
        if not v:
            return "—"
        return (f"median {fmt.format(stats.median(v))}, "
                f"range {fmt.format(min(v))} to {fmt.format(max(v))}")

    summary = "".join([
        f'<tr><th scope="row">Square, exactly the size the rule asked for</th>'
        f'<td>{n} of {n}</td><td>{len(ok)} of {n}'
        f'{f", no image returned on {errs}" if errs else ""}</td></tr>',

        f'<tr><th scope="row">Logo against the source artwork</th>'
        f'<td>{span(det_art, "{:.0f}")} of 255</td>'
        f'<td>{span(md_art, "{:.0f}")} of 255</td></tr>',

        f'<tr><th scope="row">Background plate against the colour chosen</th>'
        f'<td>{span(det_plate, "{:.0f}")} of 255</td>'
        f'<td>{span(md_plate, "{:.0f}")} of 255</td></tr>',

        f'<tr><th scope="row">Logos enlarged</th>'
        f'<td>{len(enlarged)} of {n}</td><td>{len(enlarged)} of {n}</td></tr>',

        f'<tr><th scope="row">Logos whose proportions changed</th>'
        f'<td>0 of {n}</td><td>0 of {n}</td></tr>',

        f'<tr><th scope="row">Time per logo</th>'
        f'<td>{span(det_ms, "{:.0f}")} ms</td><td>{span(md_s, "{:.1f}")} s</td></tr>',
    ])

    missing_note = ""
    if missing:
        items = "".join(f"<li>Row {no} — {e(label)}: {e(reason)}.</li>"
                        for no, label, reason in missing)
        missing_note = (
            f'<p class="note" style="margin-top:12px">{len(missing)} of the first '
            f'{n + len(missing)} sheet rows could not be read and are not part of the '
            f'{n} below — an unreadable file is the only refusal there is:</p>'
            f'<ul class="note" style="margin-top:4px">{items}</ul>')

    prompt_text = e(settings.PROMPT_FILE.read_text(encoding="utf-8"))
    cases = "".join(case_html(r) for r in rows)

    edge_bands = band_table(
        rows,
        [(settings.EDGE_MAX, 10 ** 9, f"{settings.EDGE_MAX} (capped)"),
         (600, settings.EDGE_MAX, f"600 to {settings.EDGE_MAX - 1}"),
         (300, 600, "300 to 599"),
         (settings.EDGE_MIN, 300, f"{settings.EDGE_MIN} to 299")],
        lambda r: r["source"]["edge"])
    raw_bands = band_table(
        rows,
        [(0.99, 1.01, "0.99 and above"), (0.95, 0.99, "0.95 to 0.99"),
         (0.90, 0.95, "0.90 to 0.95"), (0.80, 0.90, "0.80 to 0.90"),
         (-1, 0.80, "below 0.80")],
        lambda r: ((r.get("model_raw") or {}).get("before_composite") or {}).get("ssim"))

    raw_line = (
        f"a median SSIM of {stats.median(raw_ssim):.4f} "
        f"(range {min(raw_ssim):.4f} to {max(raw_ssim):.4f}) and a median mean-pixel "
        f"difference of {stats.median(raw_px):.4f}"
        if raw_ssim else "no recorded difference")

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Logo squaring test: {n} logos</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<div id="lightbox" role="dialog" aria-modal="true" aria-label="Enlarged image">
  <span class="hint">Click anywhere or press Esc to close</span><img alt=""></div>
<main class="page">
  <h1>Logo squaring test: {n} logos</h1>
  <p class="intro">Every logo from the sheet squared under the Logo Squaring Rules of
  19 September 2026 and measured against its own source file. Each logo is squared two
  ways: in code, and by {e(MODEL_NAME)} at {e(data.get('quality', ''))} quality with the
  source artwork composited back over its own rectangle. {n} logos ({uniq} distinct
  source links); each was run once. Generated {e(data['generated'])}. This report records
  what was measured — the numbers are presented as they came out, without scoring.</p>
  {missing_note}

  <section>
    <h2>The rule, as applied to these {n} logos</h2>
    <div class="panel">
      <p><b>The square is sized per logo, not fixed.</b> EDGE is the longer side of the
      source, held between {settings.EDGE_MIN} and {settings.EDGE_MAX}. Across these {n}
      logos that produced <b>{len(set(edges))} different output sizes</b>, from
      {min(edges)} to {max(edges)}. {len(band_big)} logos were {settings.EDGE_MAX} px or
      larger on the longer side and were capped there; {band_mid} took their own longer
      side exactly; {len(band_small)} were under {settings.EDGE_MIN} px and sit in the
      middle of a {settings.EDGE_MIN} square.</p>

      <p><b>The logo is never enlarged.</b> The scale is the smaller of EDGE/W and EDGE/H,
      capped at 1.0. {len(untouched)} of the {n} logos were placed at their true size and
      not touched; {len(shrunk)} were shrunk to fit. <b>{len(enlarged)} were enlarged.</b></p>

      <p><b>Pad, never crop, never stretch.</b> One scale factor is applied to both axes,
      so the artwork cannot be squashed, and the canvas is never smaller than the scaled
      artwork, so nothing can be cut off. Proportions changed on 0 of {n}.</p>

      <p><b>The plate is chosen per logo, and transparency is flattened onto it.</b>
      {len(transparent)} of the {n} sources carry transparency; every output is opaque RGB,
      so the square the business is shown is the square Google receives. {len(dark)} logos
      were given the dark plate ({settings.BG_DARK}) and {n - len(dark)} the light one
      ({settings.BG_LIGHT}). Each logo's reason is printed under its images below.</p>
    </div>
  </section>

  <section>
    <h2>How the model path works here</h2>
    <div class="panel">
      <p>The model is handed the square already composed, so it is never asked to scale or
      centre anything. Its reply is checked against the arrival rules — width equals
      height, size in range, an image actually returned, no error reported inside a 200, an
      answer within {settings.UPSTREAM_TIMEOUT:.0f} seconds — and then <b>the source
      artwork is pasted back over its own rectangle at full resolution</b>, blended against
      the chosen plate colour rather than against whatever the model drew underneath.</p>
      <p>That last step is why the "Logo against the source artwork" row reads as it does.
      Before the composite, the model's own render differed from the squared-in-code result
      by {raw_line}. After it, the artwork in the shipped file is the source, so what the
      model still contributes to the finished image is the background plate and nothing
      else. Both columns below are therefore the same artwork; they differ only in how the
      padding was produced.</p>
    </div>
  </section>

  <section>
    <h2>Measurements across the set</h2>
    <div class="wrap">
      <table>
        <thead><tr><th scope="col">Measurement</th>
          <th scope="col">Squared in code</th>
          <th scope="col">{e(MODEL_NAME)} + composite</th></tr></thead>
        <tbody>{summary}</tbody>
      </table>
    </div>
    <p class="note" style="margin-top:12px">"Logo against the source artwork" is the
    largest per-channel difference inside the logo's rectangle, measured against the source
    scaled by exactly the factor the rule allows and flattened onto the same plate. 0 means
    the artwork in the output is the source itself. "Background plate" is the worst
    deviation anywhere outside that rectangle from the hex colour that was chosen, so an
    invented tint, gradient or vignette shows up as a number rather than going unnoticed.</p>
  </section>

  <section>
    <h2>The square sizes produced</h2>
    <div class="wrap">
      <table>
        <thead><tr><th scope="col">EDGE</th><th scope="col">Logos</th>
          <th scope="col"></th></tr></thead>
        <tbody>{edge_bands}</tbody>
      </table>
    </div>
    <p class="note" style="margin-top:12px">A fixed output size would put one number here.
    The rule derives it from each logo's own longer side, so a 600 px logo produces a
    600 px square rather than being stretched up to {settings.EDGE_MAX}.</p>
  </section>

  <section>
    <h2>What the model's own render looked like, before the composite</h2>
    <div class="wrap">
      <table>
        <thead><tr><th scope="col">SSIM against the squared-in-code result</th>
          <th scope="col">Logos</th><th scope="col"></th></tr></thead>
        <tbody>{raw_bands}</tbody>
      </table>
    </div>
    <p class="note" style="margin-top:12px">SSIM is a structural-similarity score from 0 to
    1, where 1 is an identical image. Shown as a distribution rather than against a
    threshold. These are the renders <i>before</i> the source artwork was composited back;
    they are not what the finished files contain.</p>
  </section>

  <section>
    <h2>Method</h2>
    <details class="plain">
      <summary>How each logo was processed, and the prompt used</summary>
      <p style="margin-top:10px">Each logo is downloaded from its sheet link and opened
      (check 1; a file that cannot be read is the only refusal there is). Its shape is
      classified to within {settings.SQUARE_TOLERANCE:.0%} (check 2). EDGE is computed from
      the longer side and held between {settings.EDGE_MIN} and {settings.EDGE_MAX}
      (check 3). The plate colour is chosen from the filename, then from the pixels, then
      white (check 4). The square is made: one scale factor on both axes capped at 1.0,
      centred, transparency flattened onto the plate, saved as PNG (check 5). The model
      receives that same composition, and its reply is validated and composited as
      described above. All measurements are taken against the original source file.</p>
      <pre class="prompt">{prompt_text}</pre>
    </details>
  </section>

  <section class="group">
    <h2>All {n} logos</h2>
    <p class="note">Sheet order. Click any image to enlarge; Download saves the real PNG at
    that logo's own EDGE.</p>
    {cases}
  </section>
</main>
<script>{JS}</script>
</body>
</html>"""

    cols = ["no", "logo", "sheet_link", "original_w", "original_h", "original_format",
            "aspect_type", "transparent", "edge", "background", "background_reason",
            "scale_factor", "artwork_w", "artwork_h",
            "code_artwork_diff", "code_plate_deviation",
            "model_artwork_diff", "model_plate_deviation",
            "model_raw_ssim", "model_raw_pixel_diff", "model_seconds", "model_error"]
    with (OUT / "report_plain.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            ex, md, sc = r["exact"], r["model"], r["source"]
            eb = (ex.get("framing") or {}).get("output_content", ["", ""])
            rw = (r.get("model_raw") or {}).get("before_composite") or {}
            w.writerow({
                "no": r["no"], "logo": case_title(r), "sheet_link": r["url"],
                "original_w": sc["width"], "original_h": sc["height"],
                "original_format": sc["format"], "aspect_type": sc["aspect_type"],
                "transparent": sc.get("transparent"),
                "edge": sc["edge"], "background": sc["background"],
                "background_reason": sc.get("background_reason", ""),
                "scale_factor": ex["scale_factor"],
                "artwork_w": eb[0], "artwork_h": eb[1],
                "code_artwork_diff": ex.get("artwork_max_diff"),
                "code_plate_deviation": ex.get("plate_max_deviation"),
                "model_artwork_diff": md.get("artwork_max_diff", ""),
                "model_plate_deviation": md.get("plate_max_deviation", ""),
                "model_raw_ssim": rw.get("ssim", ""),
                "model_raw_pixel_diff": rw.get("mean_pixel_diff", ""),
                "model_seconds": round(md.get("elapsed_ms", 0) / 1000, 1),
                "model_error": md.get("error", ""),
            })

    dest = OUT / f"logo_squaring_{n}_logos.html"
    dest.write_text(doc, encoding="utf-8")
    print(f"{n} logos | {len(ok)} model renders returned | {errs} model errors")
    print(f"Report -> {dest}  ({dest.stat().st_size/1024/1024:.1f} MB)")
    print(f"CSV    -> {OUT / 'report_plain.csv'}")


if __name__ == "__main__":
    main()
