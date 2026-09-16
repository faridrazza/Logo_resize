"""Read logo URLs out of an xlsx and download them into data/input.

    python scripts/fetch_sheet.py "Sample logo for resizing.xlsx" --limit 100
    python scripts/fetch_sheet.py "Logos to be resize.xlsx"

Finds the column holding the links (a header named Logo/Image/URL, else the first
column whose cells look like URLs) and, if present, a Domain column used to name
the files. Writes data/input/_manifest.tsv for the report.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx                       # noqa: E402
import openpyxl                    # noqa: E402

from config import settings        # noqa: E402

EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
    "image/tiff": ".tif", "image/svg+xml": ".svg", "image/avif": ".avif",
}
RASTER = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def slug(text: str) -> str:
    s = re.sub(r"^https?://(www\.)?", "", str(text or "").strip())
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return (s or "logo")[:40]


def read_rows(xlsx: Path, limit: int) -> list[tuple[str, str]]:
    """Return [(label, url)] for the first `limit` data rows."""
    ws = openpyxl.load_workbook(xlsx)[openpyxl.load_workbook(xlsx).sheetnames[0]]
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    if not grid:
        return []

    header = [str(c or "").strip().lower() for c in grid[0]]
    body = grid[1:] if any(h in {"logo", "domain", "url", "image", "logo link"} for h in header) else grid

    def is_url(v) -> bool:
        return str(v or "").strip().lower().startswith(("http://", "https://"))

    link_col = next((i for i, h in enumerate(header)
                     if h in {"logo", "logo link", "image", "url", "image url"}), None)
    if link_col is None:
        counts = [sum(1 for r in body[:50] if i < len(r) and is_url(r[i]))
                  for i in range(max(len(r) for r in body))]
        link_col = counts.index(max(counts))
    label_col = next((i for i, h in enumerate(header) if h in {"domain", "site", "website"}), None)

    out = []
    for r in body:
        url = str(r[link_col]).strip() if link_col < len(r) and is_url(r[link_col]) else ""
        if not url:
            continue
        label = str(r[label_col]).strip() if label_col is not None and label_col < len(r) and r[label_col] else url
        out.append((label, url))
        if limit and len(out) >= limit:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx", nargs="?", default="Sample logo for resizing.xlsx")
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows")
    args = ap.parse_args()

    rows = read_rows(Path(args.xlsx), args.limit)
    print(f"{Path(args.xlsx).name}: taking {len(rows)} logo links\n")

    lines, ok, failed, nonraster = [], 0, 0, 0
    with httpx.Client(timeout=45, follow_redirects=True,
                      headers={"User-Agent": UA, "Accept": "image/*,*/*"}) as client:
        for i, (label, url) in enumerate(rows, start=1):
            name = f"{i:03d}_{slug(label)}"
            try:
                r = client.get(url)
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                ext = EXT.get(ctype) or Path(unquote(urlparse(url).path)).suffix.lower() or ".png"
                if ext not in RASTER:
                    nonraster += 1
                    print(f"  [{i:>3}] skipped  {ext or ctype or 'unknown type'}  {label[:44]}")
                    lines.append(f"{i}\tUNSUPPORTED:{ext or ctype}\t{label}\t{url}")
                    continue
                path = settings.INPUT_DIR / f"{name}{ext}"
                path.write_bytes(r.content)
                ok += 1
                lines.append(f"{i}\t{path.name}\t{label}\t{url}")
                if i <= 5 or i % 20 == 0:
                    print(f"  [{i:>3}] {path.name:<48} {len(r.content):>9,} B")
            except Exception as exc:
                failed += 1
                print(f"  [{i:>3}] FAILED   {type(exc).__name__}  {label[:44]}")
                lines.append(f"{i}\tDOWNLOAD_FAILED\t{label}\t{url}")

    manifest = settings.INPUT_DIR / "_manifest.tsv"
    manifest.write_text("\n".join(lines), encoding="utf-8")
    print(f"\ndownloaded {ok} | unsupported format {nonraster} | failed {failed}")
    print(f"Manifest -> {manifest}")


if __name__ == "__main__":
    main()
