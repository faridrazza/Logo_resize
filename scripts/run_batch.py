"""Run the pipeline over a folder (or a list of URLs) and produce the review artefacts.

    python scripts/run_batch.py                      # every image in data/input
    python scripts/run_batch.py --mode exact
    python scripts/run_batch.py --urls urls.txt      # one URL per line

Outputs
    data/output/<name>_<edge>x<edge>_<id>.png   squared logos
    data/output/report.csv                import straight into Google Sheets
    data/output/review.html               side-by-side original vs output
"""
from __future__ import annotations

import argparse
import base64
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import service                     # noqa: E402
from config import settings        # noqa: E402

SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}

COLUMNS = [
    "no", "source", "original_w", "original_h", "original_format", "aspect_type",
    "edge", "background", "background_reason", "scale_factor",
    "output_file", "output_w", "output_h", "output_bytes", "renderer_used",
    "fell_back_in_house", "rejected_as", "composited_source",
    "phash_distance", "ssim", "mean_pixel_diff", "color_delta",
    "elapsed_ms", "note",
]


def collect(args) -> list[str]:
    if args.urls:
        return [l.strip() for l in Path(args.urls).read_text().splitlines() if l.strip()]
    folder = Path(args.input) if args.input else settings.INPUT_DIR
    return [str(p) for p in sorted(folder.iterdir()) if p.suffix.lower() in SUFFIXES]


def load(source: str) -> tuple[bytes, str]:
    if source.lower().startswith(("http://", "https://")):
        return service.fetch_url(source), Path(source.split("?")[0]).name or "logo.png"
    p = Path(source)
    return p.read_bytes(), p.name


def thumb(data: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(data).decode()


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch logo resize + fidelity report")
    ap.add_argument("--input", help="folder of logos (default: data/input)")
    ap.add_argument("--urls", help="text file with one image URL per line")
    ap.add_argument("--mode", default=None, help="exact | ai | hybrid")
    args = ap.parse_args()

    sources = collect(args)
    if not sources:
        print("No images found. Drop the logos into data/input first.")
        return

    rows, cards, review = [], [], 0
    print(f"Processing {len(sources)} logos in mode "
          f"'{args.mode or settings.DEFAULT_MODE}'...\n")

    for i, source in enumerate(sources, start=1):
        try:
            data, name = load(source)
            r = service.process(data, filename=name, mode=args.mode)
            f = r["fidelity"] or {}
            rows.append({
                "no": i, "source": source,
                "original_w": r["source"]["source_width"],
                "original_h": r["source"]["source_height"],
                "original_format": r["source"]["source_format"],
                "aspect_type": r["source"]["aspect_type"],
                "edge": r["source"]["edge"],
                "background": r["source"]["background_hex"],
                "background_reason": r["source"]["background_reason"],
                "scale_factor": r["source"]["scale_factor"],
                "output_file": r["output"]["filename"],
                "output_w": r["output"]["width"], "output_h": r["output"]["height"],
                "output_bytes": r["output"]["bytes"],
                "renderer_used": r["renderer_used"],
                "fell_back_in_house": r["fell_back_in_house"],
                "rejected_as": r["rejected_as"] or "",
                "composited_source": r["composited_source"],
                "phash_distance": f.get("phash_distance", ""),
                "ssim": f.get("ssim", ""),
                "mean_pixel_diff": f.get("mean_pixel_diff", ""),
                "color_delta": f.get("color_delta", ""),
                "elapsed_ms": r["elapsed_ms"],
                "note": r["note"] or "",
            })
            if r["fell_back_in_house"]:
                review += 1
            state = r["rejected_as"] or r["renderer_used"]
            cards.append((i, name, thumb(data), thumb(base64.b64decode(r["image_base64"])),
                          r["source"], state, f))
            print(f"  [{i:>3}/{len(sources)}] {name:<40} "
                  f"EDGE {r['source']['edge']}  {state}")
        except Exception as exc:                                   # keep the run going
            rows.append({c: "" for c in COLUMNS} | {"no": i, "source": source,
                                                    "rejected_as": "unreadable",
                                                    "note": str(exc)})
            print(f"  [{i:>3}/{len(sources)}] {source:<40} ERROR  {exc}")

    csv_path = settings.OUTPUT_DIR / "report.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    html_path = settings.OUTPUT_DIR / "review.html"
    html_path.write_text(build_html(cards), encoding="utf-8")

    print(f"\nDone. {len(rows)} rows, {review} flagged for review.")
    print(f"  CSV    -> {csv_path}   (File > Import in Google Sheets)")
    print(f"  Review -> {html_path}")


def build_html(cards) -> str:
    body = []
    for i, name, before, after, meta, state, f in cards:
        metrics = (f"pHash {f.get('phash_distance')} &middot; SSIM {f.get('ssim')} "
                   f"&middot; diff {f.get('mean_pixel_diff')}") if f else "squared in code"
        body.append(f"""
        <div class="card">
          <div class="hd"><b>#{i} {name}</b>
            <span class="tag">{state}</span></div>
          <div class="pair">
            <figure><img src="{before}"><figcaption>original
              {meta['source_width']}x{meta['source_height']} ({meta['aspect_type']})</figcaption></figure>
            <figure><img src="{after}"><figcaption>output {meta['edge']}x{meta['edge']}</figcaption></figure>
          </div>
          <div class="mt">{metrics}</div>
        </div>""")
    return f"""<!doctype html><meta charset="utf-8"><title>Logo resize review</title>
<style>
 body{{font:14px system-ui,sans-serif;margin:24px;background:#fafafa;color:#111}}
 h1{{font-size:20px}}
 .card{{background:#fff;border:1px solid #e4e4e7;border-radius:10px;padding:14px;margin:0 0 14px}}
 .hd{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
 .pair{{display:flex;gap:18px;flex-wrap:wrap}}
 figure{{margin:0;text-align:center}}
 figure img{{max-width:300px;max-height:300px;background:
   repeating-conic-gradient(#eee 0 25%,#fff 0 50%) 50%/16px 16px;border:1px solid #e4e4e7;border-radius:6px}}
 figcaption{{color:#666;font-size:12px;margin-top:6px}}
 .mt{{color:#666;font-size:12px;margin-top:10px}}
 .tag{{font-size:11px;padding:3px 8px;border-radius:20px}}
 .tag{{background:#eef0f2;color:#333}}
</style>
<h1>Logo squaring review &mdash; original vs squared output</h1>
{''.join(body)}"""


if __name__ == "__main__":
    main()
