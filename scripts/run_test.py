"""Run every logo through both paths and record the evidence.

    python scripts/run_test.py              # all inputs, both renders
    python scripts/run_test.py --workers 6

For each logo it produces
    data/output/exact/<name>.png    deterministic 300x300 resize
    data/output/model/<name>.png    gpt-image-2.5-flare 300x300 render
    data/output/results.json        every measurement, for build_report.py

Measurements are taken against the source, not against a stored expectation, so
the numbers in the report are reproducible by anyone re-running this script.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                 # noqa: E402
from PIL import Image              # noqa: E402

import service                     # noqa: E402
from config import settings        # noqa: E402
from verify import compare         # noqa: E402

SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
EXACT_DIR = settings.OUTPUT_DIR / "exact"
MODEL_DIR = settings.OUTPUT_DIR / "model"
PREVIOUS_MS: dict[str, float] = {}   # model timings carried over on --reuse


# ------------------------------------------------------------------ measurement

def flatten(im: Image.Image) -> Image.Image:
    bg = Image.new("RGB", im.size, (255, 255, 255))
    im = im.convert("RGBA")
    bg.paste(im, (0, 0), im)
    return bg


def content_box(im: Image.Image, bg):
    """Bounding box of the actual artwork inside the canvas.

    The cut-off is a fraction of the image's own strongest deviation from the
    background rather than a fixed number, so a soft-edged logo on a dark plate
    and a hard-edged one on white are measured on the same terms. A fixed cut-off
    lands mid-edge on soft artwork and moves the box by several pixels.
    """
    a = np.asarray(im.convert("RGBA"))
    if bg is None:
        dev = a[:, :, 3].astype(np.int32)
    else:
        dev = np.abs(a[:, :, :3].astype(np.int32) - np.array(bg)).sum(2)
    peak = int(dev.max())
    if peak == 0:
        return None
    mask = dev > max(8, peak * 0.10)
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def dominant(im: Image.Image, top: int = 6, apart: float = 40.0, min_share: float = 0.03):
    """The artwork's own brand colours: exact RGB values, most common first.

    Counted on the real pixels (strided, never resampled) so the share of each
    colour is accurate. Colours covering less than `min_share` of the artwork are
    anti-aliased edge blends or gradient steps rather than brand colours, and
    near-duplicates are skipped, so the list is distinct fills rather than
    several shades of one.
    """
    px = np.asarray(flatten(im)).reshape(-1, 3)
    if len(px) > 2_000_000:             # uniform stride keeps the counts honest
        px = px[:: len(px) // 2_000_000 + 1]
    keep = px[px.sum(1) < 720]          # drop the white/near-white background
    if not len(keep):
        keep = px
    u, c = np.unique(keep, axis=0, return_counts=True)
    order = np.argsort(-c)
    picked: list[tuple[int, int, int]] = []
    for i in order:
        if c[i] / len(keep) < min_share and picked:
            break
        col = tuple(int(v) for v in u[i])
        if all(np.linalg.norm(np.array(col) - np.array(p)) >= apart for p in picked):
            picked.append(col)
        if len(picked) == top:
            break
    return picked


def colour_survival(src: Image.Image, out: Image.Image):
    """For each dominant source colour, how far is the closest colour in the output."""
    px = np.asarray(flatten(out)).reshape(-1, 3).astype(np.float32)
    worst, per = 0.0, []
    for c in dominant(src):
        d = float(np.min(np.linalg.norm(px - np.array(c, np.float32), axis=1)))
        per.append({"colour": c, "delta": round(d, 1)})
        worst = max(worst, d)
    return round(worst, 1), per


def framing(src: Image.Image, out: Image.Image, bg, edge: int):
    """Was the artwork scaled by one uniform factor and left uncropped?"""
    sb, ob = content_box(src, bg), content_box(out, bg)
    if not sb or not ob:
        return None
    sw, sh = sb[2] - sb[0] + 1, sb[3] - sb[1] + 1
    ow, oh = ob[2] - ob[0] + 1, ob[3] - ob[1] + 1
    src_ar, out_ar = sw / sh, ow / oh
    off_x = (ob[0] + ob[2] + 1) / 2 - edge / 2
    off_y = (ob[1] + ob[3] + 1) / 2 - edge / 2

    # Where the sizing rule must put the artwork: one scale factor, capped at 1.0.
    scale = service.artwork_scale(src.size[0], src.size[1], edge)
    ew, eh = sw * scale, sh * scale
    return {
        "source_content": [sw, sh],
        "output_content": [ow, oh],
        "expected_content": [round(ew, 1), round(eh, 1)],
        "px_error": [round(ow - ew, 1), round(oh - eh, 1)],
        "source_ar": round(src_ar, 4),
        "output_ar": round(out_ar, 4),
        "ar_error_pct": round(abs(src_ar - out_ar) / src_ar * 100, 2),
        "centre_offset_px": [round(off_x, 1), round(off_y, 1)],
        "touches_edge": ow >= edge or oh >= edge,
    }


def uniform_scale_max_diff(src: Image.Image, out: Image.Image, bg, edge: int) -> int:
    """Strictest possible check on the deterministic path.

    Rebuilds the expected result here, independently of service.square_canvas:
    one scale factor for both axes, centred on the canvas. Returns the largest
    per-channel difference against the shipped file. 0 means the output is that
    operation and nothing else — no stretch, no crop, no redraw. A stretched or
    cropped render could not score 0, so this is a real check, not a restatement.
    """
    size = edge
    left, top, nw, nh = service.artwork_box(src.size[0], src.size[1], size)
    expected = Image.new("RGBA", (size, size), service.hex_to_rgb(bg) + (255,))
    art = src.resize((nw, nh), Image.LANCZOS).convert("RGBA")
    expected.alpha_composite(art, (left, top))
    expected = expected.convert("RGB")
    a = np.asarray(expected, dtype=np.int16)
    b = np.asarray(out.convert("RGB"), dtype=np.int16)
    if a.shape != b.shape:
        return 255
    return int(np.abs(a - b).max())


def artwork_max_diff(src: Image.Image, out: Image.Image, edge: int, bg_hex: str) -> int:
    """Largest channel difference inside the artwork rectangle.

    Compares the shipped file's artwork area against the source scaled by exactly
    the factor STEP 5 allows. 0 means the artwork in the output IS the source, so
    nothing was redrawn, re-typeset or re-coloured - the claim "no changes to the
    logo" is then a measurement rather than an opinion.
    """
    left, top, nw, nh = service.artwork_box(src.size[0], src.size[1], edge)
    art = src.resize((nw, nh), Image.LANCZOS).convert("RGBA")
    # Flatten onto the plate that was actually used, not onto white: an
    # anti-aliased edge pixel blended against near-black is a different colour
    # from the same pixel blended against white, and comparing the two would
    # report a difference that is not there.
    want = Image.new("RGBA", (nw, nh), service.hex_to_rgb(bg_hex) + (255,))
    want.alpha_composite(art)
    got = out.convert("RGB").crop((left, top, left + nw, top + nh))
    a = np.asarray(want.convert("RGB"), dtype=np.int16)
    b = np.asarray(got, dtype=np.int16)
    if a.shape != b.shape:
        return 255
    return int(np.abs(a - b).max())


def plate_max_deviation(out: Image.Image, src: Image.Image, edge: int, bg_hex: str) -> int:
    """How far the padding strays from the flat colour that was asked for.

    The rules want the square created "in the chosen colour" - one uniform plate
    with nothing on it. This measures the worst channel deviation anywhere
    outside the artwork rectangle, so an invented gradient, tint or vignette
    shows up as a number instead of going unnoticed.
    """
    left, top, nw, nh = service.artwork_box(src.size[0], src.size[1], edge)
    a = np.asarray(out.convert("RGB"), dtype=np.int16)
    want = np.array(service.hex_to_rgb(bg_hex), dtype=np.int16)
    mask = np.ones(a.shape[:2], dtype=bool)
    mask[top:top + nh, left:left + nw] = False
    if not mask.any():
        return 0
    return int(np.abs(a[mask] - want).max())


def png_bytes(im: Image.Image) -> bytes:
    return service.to_png_bytes(im)


# ------------------------------------------------------------------- single run

def run_one(idx: int, path: Path, url: str, reuse: bool = False, label: str = "",
            fill: bool = False) -> dict:
    """reuse=True re-measures the PNGs already on disk instead of calling the model.

    fill=True reuses a cached render when there is one and calls the model only for
    the logos that are missing, so a transient network failure can be retried
    without paying for the whole batch again.
    """
    row: dict = {"no": idx, "file": path.name, "url": url, "label": label}
    src = service.open_image(path.read_bytes())
    edge = service.compute_edge(*src.size)
    bg, bg_reason = service.choose_background(src, url or path.name)
    row["source"] = {
        "width": src.size[0], "height": src.size[1],
        "format": (src.format or "PNG").upper(),
        "aspect_type": service.classify_aspect(*src.size),
        "aspect_ratio": round(src.size[0] / src.size[1], 3),
        "transparent": service.has_transparency(src),
        "edge": edge,
        "background": bg,
        "background_reason": bg_reason,
        "bytes": path.stat().st_size,
    }

    # ---- deterministic path
    t0 = time.perf_counter()
    exact = service.render_square(src, edge, bg)
    exact_ms = round((time.perf_counter() - t0) * 1000, 1)
    (EXACT_DIR / f"{path.stem}.png").write_bytes(png_bytes(exact))

    worst, per = colour_survival(src, exact)
    row["exact"] = {
        "file": f"exact/{path.stem}.png",
        "size": list(exact.size),
        "bytes": len(png_bytes(exact)),
        "elapsed_ms": exact_ms,
        "framing": framing(src, exact, service.hex_to_rgb(bg), edge),
        "colour_worst_delta": worst,
        "colour_detail": per,
        "scale_factor": round(service.artwork_scale(*src.size, edge), 4),
        "uniform_scale_max_diff": uniform_scale_max_diff(src, exact, bg, edge),
        "artwork_max_diff": artwork_max_diff(src, exact, edge, bg),
        "plate_max_deviation": plate_max_deviation(exact, src, edge, bg),
    }

    # ---- model path
    t0 = time.perf_counter()
    try:
        cached = MODEL_DIR / f"{path.stem}.png"
        if fill and cached.exists():
            reuse = True
        if reuse:
            if not cached.exists():
                raise FileNotFoundError(f"no cached model render for {path.name}")
            model = Image.open(cached).convert("RGB")
            model_ms = PREVIOUS_MS.get(path.name, 0.0)
            rejected = None
        else:
            render = service.render_ai(src, edge, bg, {
                "source_width": src.size[0], "source_height": src.size[1],
                "aspect_type": row["source"]["aspect_type"],
            })
            service.validate_square(render, edge, png_bytes(render))
            row.setdefault("model_raw", {})["before_composite"] =                 compare(exact, render)
            model = (service.composite_source(render, src, edge, bg)
                     if settings.COMPOSITE_SOURCE else render)
            model_ms = round((time.perf_counter() - t0) * 1000, 1)
            rejected = None
        (MODEL_DIR / f"{path.stem}.png").write_bytes(png_bytes(model))
        m_worst, m_per = colour_survival(src, model)
        row["model"] = {
            "file": f"model/{path.stem}.png",
            "size": list(model.size),
            "bytes": len(png_bytes(model)),
            "elapsed_ms": model_ms,
            "framing": framing(src, model, service.hex_to_rgb(bg), edge),
            "colour_worst_delta": m_worst,
            "colour_detail": m_per,
            "composited": bool(settings.COMPOSITE_SOURCE),
            "rejected_as": rejected,
            "uniform_scale_max_diff": uniform_scale_max_diff(src, model, bg, edge),
            "artwork_max_diff": artwork_max_diff(src, model, edge, bg),
            "plate_max_deviation": plate_max_deviation(model, src, edge, bg),
            "vs_exact": compare(exact, model),
        }
    except Exception as exc:
        row["model"] = {
            "error": f"{type(exc).__name__}: {exc}",
            "rejected_as": getattr(exc, "code", None),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    return row


# ------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reuse", action="store_true",
                    help="re-measure the saved renders instead of calling the model")
    ap.add_argument("--fill", action="store_true",
                    help="keep saved renders, call the model only for the missing ones")
    args = ap.parse_args()

    EXACT_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    urls = {}
    # manifest: "no \t filename \t [label] \t url"  (the label column is optional)
    labels: dict[str, str] = {}
    manifest = settings.INPUT_DIR / "_manifest.tsv"
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                _, fname, label, url = parts
                labels[fname] = label
            elif len(parts) == 3:
                _, fname, url = parts
            else:
                continue
            urls[fname] = url

    prior = settings.OUTPUT_DIR / "results.json"
    if args.reuse and prior.exists():
        for r in json.loads(prior.read_text(encoding="utf-8"))["rows"]:
            PREVIOUS_MS[r["file"]] = r.get("model", {}).get("elapsed_ms", 0.0)

    files = [p for p in sorted(settings.INPUT_DIR.iterdir()) if p.suffix.lower() in SUFFIXES]
    if args.limit:
        files = files[: args.limit]
    print(f"{len(files)} logos | model {settings.IMAGE_MODEL} | {args.workers} workers\n")

    started = time.perf_counter()
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_one, i, p, urls.get(p.name, ""), args.reuse,
                        labels.get(p.name, ""), args.fill): (i, p)
            for i, p in enumerate(files, start=1)
        }
        for fut in futures:
            pass
        for fut, (i, p) in list(futures.items()):
            try:
                row = fut.result()
            except Exception as exc:
                row = {"no": i, "file": p.name, "url": urls.get(p.name, ""),
                       "label": labels.get(p.name, ""),
                       "fatal": f"{type(exc).__name__}: {exc}"}
            rows.append(row)
            m = row.get("model", {})
            state = "ERROR" if "error" in m or "fatal" in row else (
                "match" if m.get("vs_exact", {}).get("passed") else "differs")
            print(f"  [{len(rows):>3}/{len(files)}] {p.name:<46} {state:<8} "
                  f"{m.get('elapsed_ms', 0)/1000:>5.1f}s")

    rows.sort(key=lambda r: r["no"])
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "model": settings.IMAGE_MODEL,
        "target": f"per-logo EDGE, {settings.EDGE_MIN}-{settings.EDGE_MAX}",
        "composite_source": settings.COMPOSITE_SOURCE,
        "quality": settings.IMAGE_QUALITY,
        "total_seconds": round(time.perf_counter() - started, 1),
        "rows": rows,
    }
    out = settings.OUTPUT_DIR / "results.json"
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\nDone in {payload['total_seconds']}s -> {out}")


if __name__ == "__main__":
    main()
