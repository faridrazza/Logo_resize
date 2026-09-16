"""Second pass over the rows fetch_sheet.py could not use.

    python scripts/retry_failed.py

Two recoveries:
  * SVG sources are rasterised (svglib + reportlab, pure Python) at a size large
    enough that the 300 x 300 fit never upscales.
  * HTTP refusals are retried with a Referer from the site itself, which is what
    most hotlink protection actually checks.

Updates data/input/_manifest.tsv in place.
"""
from __future__ import annotations

import io
import re
import numpy as np
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx                       # noqa: E402
from PIL import Image              # noqa: E402

from config import settings        # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
RENDER_MIN = 900   # px on the long edge, so the 300 px fit is always a downscale


def slug(text: str) -> str:
    s = re.sub(r"^https?://(www\.)?", "", str(text or "").strip())
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower() or "logo")[:40]


def rasterise_svg(data: bytes) -> Image.Image:
    """Rasterise an SVG while recovering its transparency.

    renderPM can only draw onto an opaque background, which destroys logos that
    are white artwork on transparency (they come back as a blank white block).
    Drawing the same SVG twice, on white and on black, gives two equations per
    pixel that solve exactly for the original colour and alpha:

        on white:  Cw = C*a + 255*(1-a)
        on black:  Cb = C*a
        =>         a  = 1 - (Cw - Cb)/255,   C = Cb / a
    """
    from reportlab.graphics import renderPM
    from svglib.svglib import svg2rlg

    def draw(bg: int) -> np.ndarray:
        drawing = svg2rlg(io.BytesIO(data))
        if drawing is None or not drawing.width or not drawing.height:
            raise ValueError("SVG has no usable drawing size")
        scale = max(1.0, RENDER_MIN / max(drawing.width, drawing.height))
        drawing.scale(scale, scale)
        drawing.width *= scale
        drawing.height *= scale
        buf = io.BytesIO()
        renderPM.drawToFile(drawing, buf, fmt="PNG", bg=bg)
        return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"), dtype=np.float64)

    on_white, on_black = draw(0xFFFFFF), draw(0x000000)
    alpha = 1.0 - (on_white - on_black).mean(axis=2) / 255.0
    alpha = np.clip(alpha, 0.0, 1.0)
    safe = np.clip(alpha, 1e-6, None)[:, :, None]
    colour = np.clip(on_black / safe, 0, 255)
    out = np.dstack([colour, alpha[:, :, None] * 255.0]).astype(np.uint8)
    img = Image.fromarray(out, "RGBA")
    if img.getchannel("A").getextrema()[1] == 0:
        raise ValueError("SVG rasterised empty")
    return img


def main() -> None:
    manifest = settings.INPUT_DIR / "_manifest.tsv"
    lines = manifest.read_text(encoding="utf-8").splitlines()
    out, fixed = [], 0

    with httpx.Client(timeout=45, follow_redirects=True) as client:
        for line in lines:
            parts = line.split("\t")
            if len(parts) != 4 or not parts[1].startswith(("UNSUPPORTED", "DOWNLOAD_FAILED")):
                out.append(line)
                continue
            no, status, label, url = parts
            origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
            headers = {"User-Agent": UA, "Accept": "image/*,*/*",
                       "Referer": origin, "Accept-Language": "en-US,en;q=0.9"}
            try:
                r = client.get(url, headers=headers)
                r.raise_for_status()
                name = f"{int(no):03d}_{slug(label)}.png"
                if status.startswith("UNSUPPORTED") and ".svg" in status.lower():
                    img = rasterise_svg(r.content)
                    img.save(settings.INPUT_DIR / name)
                    how = f"SVG rasterised {img.size[0]}x{img.size[1]}"
                else:
                    img = Image.open(io.BytesIO(r.content))
                    img.load()
                    img.save(settings.INPUT_DIR / name)
                    how = f"downloaded with Referer {img.size[0]}x{img.size[1]}"
                out.append(f"{no}\t{name}\t{label}\t{url}")
                fixed += 1
                print(f"  [{no:>3}] recovered  {how:<34} {label[:40]}")
            except Exception as exc:
                out.append(line)
                print(f"  [{no:>3}] still failing  {type(exc).__name__}: {str(exc)[:60]}")

    manifest.write_text("\n".join(out), encoding="utf-8")
    usable = sum(1 for l in out if not l.split("\t")[1].startswith(("UNSUPPORTED", "DOWNLOAD_FAILED")))
    print(f"\nrecovered {fixed} | usable logos now {usable} of {len(out)}")


if __name__ == "__main__":
    main()
