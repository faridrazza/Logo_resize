"""Logo resize service.

Pipeline
    1. decode source, normalise orientation/colour mode
    2. deterministic "contain" fit into TARGET_SIZE x TARGET_SIZE  -> pixel-exact reference
    3. optional image-model pass, seeded with the composed square render
    4. fidelity verification of the model output against the reference
    5. hybrid mode keeps the model output only when it passes, else falls back

The deterministic step never alters design, text, colour or proportions: it only
scales uniformly and pads. That is what makes "the logo must not change" provable.
"""
from __future__ import annotations

import base64
import io
import time
import uuid
from collections import Counter
from pathlib import Path

import httpx
from PIL import Image, ImageChops, ImageOps

from config import settings
from verify import compare

Image.MAX_IMAGE_PIXELS = 200_000_000
MODES = ("exact", "ai", "hybrid")


class LogoError(Exception):
    """Any recoverable problem with the request or the upstream model."""


# --------------------------------------------------------------------------- io

def fetch_url(url: str) -> bytes:
    try:
        r = httpx.get(url, timeout=settings.REQUEST_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LogoError(f"Could not download image: {exc}") from exc
    return r.content


def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise LogoError(f"Unsupported or corrupt image file: {exc}") from exc
    fmt = img.format
    img = ImageOps.exif_transpose(img)
    if img.mode in ("P", "LA", "PA"):
        img = img.convert("RGBA")
    elif img.mode in ("CMYK", "L", "1", "I", "F"):
        img = img.convert("RGB")
    elif img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    img.format = fmt
    return img


def to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ------------------------------------------------------------------ composition

def classify_aspect(w: int, h: int) -> str:
    ratio = w / h
    if 0.95 <= ratio <= 1.05:
        return "square"
    return "horizontal" if ratio > 1 else "vertical"


def detect_background(img: Image.Image):
    """Return None for transparent padding, else the (r, g, b) to pad with."""
    if settings.OUTPUT_BACKGROUND == "transparent":
        return None
    if settings.OUTPUT_BACKGROUND == "white":
        return (255, 255, 255)
    if img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 250:
        return None  # source already has transparency -> keep it
    rgb = img.convert("RGB")
    w, h = rgb.size
    corners = [rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    color, count = Counter(corners).most_common(1)[0]
    return color if count >= 3 else (255, 255, 255)


def trim_border(img: Image.Image, bg) -> Image.Image:
    """Strip a uniform border. Off by default - framing is part of the asset."""
    rgb = img.convert("RGB")
    ref = Image.new("RGB", rgb.size, bg or (255, 255, 255))
    diff = ImageChops.difference(rgb, ref)
    bbox = ImageChops.add(diff, diff, 1.0, -settings.TRIM_TOLERANCE).getbbox()
    return img.crop(bbox) if bbox else img


def fit_to_square(img: Image.Image, size: int, bg, allow_upscale: bool) -> Image.Image:
    """Uniform 'contain' scale + centred padding. No distortion, ever."""
    w, h = img.size
    scale = min(size / w, size / h)
    if not allow_upscale:
        scale = min(scale, 1.0)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = img.resize((nw, nh), Image.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0) if bg is None else bg + (255,))
    canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2), resized)
    return canvas


def render_exact(img: Image.Image, size: int, bg) -> Image.Image:
    source = trim_border(img, bg) if settings.TRIM_BORDER else img
    return fit_to_square(source, size, bg, settings.ALLOW_UPSCALE)


# -------------------------------------------------------------------- ai branch

def _client():
    if not settings.OPENAI_API_KEY:
        raise LogoError("OPENAI_API_KEY is not set - use mode=exact or configure .env")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LogoError("openai package is not installed") from exc
    # base_url is always explicit: an empty OPENAI_BASE_URL in the environment
    # would otherwise be picked up by the SDK and produce a protocol-less URL.
    return OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL or "https://api.openai.com/v1",
        timeout=settings.REQUEST_TIMEOUT,
        max_retries=settings.MODEL_MAX_RETRIES,
    )


def build_prompt(ctx: dict) -> str:
    try:
        template = settings.PROMPT_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogoError(f"Prompt file unreadable: {exc}") from exc
    return template.format(**ctx)


def _content_size(src_size, canvas: int):
    w, h = src_size
    scale = min(canvas / w, canvas / h)
    return max(1, round(w * scale)), max(1, round(h * scale))


def _rejected_param(exc: Exception, candidates: dict) -> str | None:
    """Which optional parameter did the API reject, if any?

    Model support differs per model (gpt-image-2.5-flare has no input_fidelity,
    older deployments have no output_format), so the offending key is dropped and
    the call retried instead of hard-coding a per-model parameter matrix.
    """
    param = getattr(getattr(exc, "body", None), "get", lambda _: None)("param")
    if isinstance(param, str) and param in candidates:
        return param
    msg = str(exc).lower()
    for key in candidates:
        if f"'{key}'" in msg and any(
            s in msg for s in ("not support", "unsupported", "unknown", "unexpected", "invalid")
        ):
            return key
    return None


def render_ai(source: Image.Image, bg, meta: dict) -> Image.Image:
    """Send the composed square render to the image model and get it back."""
    work = int(settings.IMAGE_WORK_SIZE.split("x")[0])
    seed_png = to_png_bytes(fit_to_square(source, work, bg, allow_upscale=True))
    content_w, content_h = _content_size(source.size, work)
    prompt = build_prompt(
        {
            "canvas_size": work,
            "source_width": meta["source_width"],
            "source_height": meta["source_height"],
            "aspect_type": meta["aspect_type"],
            "content_width": content_w,
            "content_height": content_h,
            "background_mode": "transparent" if bg is None else f"solid rgb{bg}",
        }
    )

    base = {
        "model": settings.IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "size": settings.IMAGE_WORK_SIZE,
    }
    optional = {
        "quality": settings.IMAGE_QUALITY,
        "input_fidelity": settings.IMAGE_INPUT_FIDELITY,
        "output_format": "png",
        "background": "transparent" if bg is None else "opaque",
    }

    client = _client()
    result, dropped = None, []
    for _ in range(len(optional) + 1):
        try:
            result = client.images.edit(
                image=("logo.png", io.BytesIO(seed_png), "image/png"), **base, **optional
            )
            break
        except Exception as exc:
            key = _rejected_param(exc, optional)
            if key is None:
                raise LogoError(f"Image model call failed: {exc}") from exc
            optional.pop(key)
            dropped.append(key)
    if result is None:
        raise LogoError("Image model rejected every parameter combination")
    if dropped:
        meta["model_params_dropped"] = dropped

    item = result.data[0]
    if getattr(item, "b64_json", None):
        raw = base64.b64decode(item.b64_json)
    elif getattr(item, "url", None):
        raw = fetch_url(item.url)
    else:
        raise LogoError("Image model returned no image data")
    return open_image(raw)


# ----------------------------------------------------------------- entry point

def process(data: bytes, filename: str = "logo.png", mode=None, save=None) -> dict:
    """Resize one logo to TARGET_SIZE x TARGET_SIZE and report what happened."""
    started = time.perf_counter()
    mode = (mode or settings.DEFAULT_MODE).lower()
    if mode not in MODES:
        raise LogoError(f"mode must be one of {', '.join(MODES)}")

    src = open_image(data)
    size = settings.TARGET_SIZE
    bg = detect_background(src)
    meta = {
        "filename": filename,
        "source_width": src.size[0],
        "source_height": src.size[1],
        "source_format": (src.format or "UNKNOWN").upper(),
        "aspect_type": classify_aspect(*src.size),
        "background": "transparent" if bg is None else f"rgb{bg}",
    }

    reference = render_exact(src, size, bg)
    final, metrics, used, fallback, note = reference, None, "exact", False, None

    if mode in ("ai", "hybrid"):
        try:
            candidate = fit_to_square(render_ai(src, bg, meta), size, bg, allow_upscale=True)
            metrics = compare(reference, candidate)
            if mode == "ai" or metrics["passed"]:
                final, used = candidate, "ai"
            else:
                fallback = True
                note = "Model output failed the fidelity check; pixel-exact result used."
        except LogoError as exc:
            if mode == "ai":
                raise
            fallback, note = True, f"{exc}. Pixel-exact result used."

    out_name = f"{Path(filename).stem or 'logo'}_{size}x{size}_{uuid.uuid4().hex[:8]}.png"
    png = to_png_bytes(final)
    out_path = None
    if settings.SAVE_OUTPUT if save is None else save:
        out_path = settings.OUTPUT_DIR / out_name
        out_path.write_bytes(png)

    return {
        "status": "ok",
        "mode_requested": mode,
        "renderer_used": used,
        "fell_back_to_exact": fallback,
        "note": note,
        "source": meta,
        "output": {
            "width": final.size[0],
            "height": final.size[1],
            "format": "PNG",
            "filename": out_name,
            "path": str(out_path) if out_path else None,
            "bytes": len(png),
        },
        "fidelity": metrics,
        "verdict": _verdict(used, metrics),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "image_base64": base64.b64encode(png).decode(),
    }


def _verdict(used: str, metrics) -> str:
    if used == "exact":
        return "UNCHANGED"           # mathematically a uniform scale + pad
    if metrics and metrics.get("passed"):
        return "UNCHANGED_VERIFIED"  # model output cleared every fidelity gate
    return "REVIEW"
