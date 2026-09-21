"""Logo squaring service - Logo Squaring Rules, 2026-09-19.

Google's Performance Max logo slot is square and will not take anything else.
Most businesses own a wordmark, several times wider than it is tall. Cropping
publishes half a brand name; giving up puts a product photograph in the logo
slot. So the logo is padded onto a square background instead: the artwork is
untouched and the square is the empty space around it.

The five checks, in order
    1. can the file be read at all        -> the only refusal
    2. is it already square               -> to within 5 percent
    3. how big is the square going to be  -> clamp(max(w, h), 128, 1200)
    4. which background colour            -> filename, then pixels, then white
    5. make it                            -> pad, never crop, never stretch

Scaling is capped at 1.0 throughout: a logo is never enlarged, because enlarging
blurs it. Transparency is flattened onto the chosen colour, or the finished
square would show whatever sits behind it in the advert.
"""
from __future__ import annotations

import base64
import io
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
from PIL import Image, ImageChops, ImageOps

from config import settings
from verify import compare

Image.MAX_IMAGE_PIXELS = 200_000_000
MODES = ("exact", "ai", "hybrid")


class LogoError(Exception):
    """Any recoverable problem with the request or the upstream model."""


class UnreadableLogo(LogoError):
    """Check 1 failed: the file is corrupt or is not an image.

    The only reason a logo is refused. There is no size at which we refuse:
    Google's 128 px minimum applies to the image, not to the logo inside it.
    """


class UpstreamRejected(LogoError):
    """The renderer answered, but the answer did not survive the arrival checks.

    Carries one of the rejection codes from the rules so the caller can record
    which check failed before falling back to squaring the logo in-house.
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- io

def fetch_url(url: str) -> bytes:
    try:
        r = httpx.get(url, timeout=settings.REQUEST_TIMEOUT, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LogoError(f"Could not download image: {exc}") from exc
    return r.content


def open_image(data: bytes) -> Image.Image:
    """Check 1: open the image and ask it for its width and height.

    If that fails the file is corrupt or is not an image, and it is refused.
    This is the only refusal there is.
    """
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise UnreadableLogo(f"unreadable: {exc}") from exc
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


def to_png_bytes(img: Image.Image, provenance: dict | None = None) -> bytes:
    """Encode as PNG, optionally carrying the provenance label in the file.

    A label that lives only in the JSON response is lost the moment the PNG is
    saved somewhere, and the rules require the square to stay identifiable as
    machine-made and as a copy of the original. Writing it into the PNG's own
    text chunks means the label travels with the artefact.
    """
    buf = io.BytesIO()
    kwargs = {}
    if provenance:
        from PIL.PngImagePlugin import PngInfo
        meta = PngInfo()
        meta.add_text("Software", str(provenance.get("produced_by", "")))
        meta.add_text("Comment", "Machine-made square. Not the business's own "
                                 "upload. A copy of the original logo.")
        meta.add_text("Source", str(provenance.get("copy_of") or ""))
        meta.add_text("Creation Time", str(provenance.get("created_utc", "")))
        meta.add_text("provenance", json.dumps(provenance, separators=(",", ":")))
        kwargs["pnginfo"] = meta
    img.save(buf, format="PNG", optimize=True, **kwargs)
    return buf.getvalue()


# ------------------------------------------------------------------ composition

def classify_aspect(w: int, h: int) -> str:
    """square / horizontal / vertical, to the tolerance the rules allow.

    "Square" is square to within about 5 percent, not to the pixel: a 1000x1010
    logo is square for our purposes, and insisting on exactness would push
    perfectly good logos down the horizontal path for no reason.
    """
    if abs(w - h) / max(w, h) <= settings.SQUARE_TOLERANCE:
        return "square"
    return "horizontal" if w > h else "vertical"


def compute_edge(w: int, h: int) -> int:
    """The finished square's edge, in pixels.

    The longer side, because the square has to be big enough to hold the logo at
    its real size and we never enlarge artwork; floored at EDGE_MIN because that
    is Google's minimum for the image (not for the logo inside it); capped at
    EDGE_MAX because that is what Google recommends and nothing above it helps.
    """
    return min(settings.EDGE_MAX, max(settings.EDGE_MIN, max(w, h)))


# ------------------------------------------------------------------ background

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    s = value.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise LogoError(f"Background must be a hex colour like #ffffff, got {value!r}")
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as exc:
        raise LogoError(f"Background must be a hex colour like #ffffff, got {value!r}") from exc


def rgb_to_hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


# Ordered: the compound forms are tested before the bare words they contain, so
# "logo-on-dark" (light artwork, wants a dark plate) is never read as "dark"
# (dark artwork, wants a light plate).
_NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("on-dark", "dark"), ("on_dark", "dark"), ("ondark", "dark"),
    ("for-dark", "dark"), ("dark-bg", "dark"), ("darkbg", "dark"),
    ("on-light", "light"), ("on_light", "light"), ("onlight", "light"),
    ("on-white", "light"), ("on_white", "light"), ("onwhite", "light"),
    ("light-bg", "light"), ("white-bg", "light"), ("whitebg", "light"),
    ("reversed", "dark"), ("reverse", "dark"), ("inverted", "dark"),
    ("inverse", "dark"), ("knockout", "dark"), ("ko-logo", "dark"),
    ("white", "dark"), ("blanco", "dark"),
    ("black", "light"), ("dark", "light"),
)


def hint_from_name(name: str) -> str | None:
    """What the file says about itself, if anything.

    Logo files are often named for the background they were drawn for
    (logo-on-dark, logo-white, logo-reverse). When the name says, we believe it:
    it is free and it is the designer's own intent. Returns the plate the logo
    wants - "dark" or "light" - or None when the name says nothing.
    """
    stem = Path(str(name or "").split("?")[0]).name.lower()
    for token, plate in _NAME_HINTS:
        if token in stem:
            return plate
    return None


def _luminance(arr: np.ndarray) -> np.ndarray:
    return arr[:, :, 0] * 0.299 + arr[:, :, 1] * 0.587 + arr[:, :, 2] * 0.114


def has_transparency(img: Image.Image) -> bool:
    return img.mode == "RGBA" and img.getchannel("A").getextrema()[0] < 250


def ink_luminance(img: Image.Image) -> float | None:
    """Average brightness (0-255) of the marks the designer actually drew.

    Transparent pixels carry an undefined colour that flattens to black or white
    depending on compositing order, and most logos are mostly transparent, so
    counting them would decide the plate by accident. None when nothing is drawn.
    """
    arr = np.asarray(img.convert("RGBA"), dtype=np.float32)
    drawn = arr[:, :, 3] > 8
    if not drawn.any():
        return None
    return float(_luminance(arr)[drawn].mean())


def plate_luminance(img: Image.Image) -> float:
    """Brightness of the background the source already carries.

    Measured on a two-pixel frame around the edge, which is the part of an
    opaque logo file that is background rather than artwork.
    """
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    lum = _luminance(arr)
    frame = np.concatenate([lum[:2].ravel(), lum[-2:].ravel(),
                            lum[:, :2].ravel(), lum[:, -2:].ravel()])
    return float(frame.mean())


def choose_background(img: Image.Image, source_name: str = "") -> tuple[str, str]:
    """The hex colour of the square, decided per logo.

    In the order the rules give: what the file says about itself, then what the
    pixels say, then white.

    What "what the pixels say" means depends on whether the file carries a
    background of its own, and the two cases pull in opposite directions:

    * A transparent logo is ink and nothing else. It needs the opposite plate or
      it disappears - white artwork on a white square is a blank square.
    * An opaque logo already sits on a plate its designer chose. Here the padding
      has to *continue* that plate, not contrast with it: a dark wordmark on its
      own white rectangle, padded with near-black, becomes a white rectangle
      floating on black. Measured on the test set, 21 of 93 logos are opaque and
      every one of them is wrong under the contrast rule.

    Both cases serve the same sentence in the rules - a logo must not vanish and
    must not be visibly boxed - which is why they are decided differently here.
    """
    plate = hint_from_name(source_name)
    reason = f"filename hint in {Path(str(source_name).split('?')[0]).name!r}"

    if plate is None:
        if has_transparency(img):
            lum = ink_luminance(img)
            if lum is None:
                plate, reason = "light", "nothing is drawn; white default"
            else:
                plate = "dark" if lum > 127.5 else "light"
                reason = (f"transparent source, ink luminance {lum:.0f} of 255; "
                          f"contrasting plate")
        else:
            lum = plate_luminance(img)
            plate = "light" if lum > 127.5 else "dark"
            reason = (f"opaque source, its own background {lum:.0f} of 255; "
                      f"plate continued")

    colour = settings.BG_DARK if plate == "dark" else settings.BG_LIGHT
    return colour, reason


def trim_border(img: Image.Image, bg) -> Image.Image:
    """Strip a uniform border. Off by default - framing is part of the asset."""
    rgb = img.convert("RGB")
    ref = Image.new("RGB", rgb.size, bg or (255, 255, 255))
    diff = ImageChops.difference(rgb, ref)
    bbox = ImageChops.add(diff, diff, 1.0, -settings.TRIM_TOLERANCE).getbbox()
    return img.crop(bbox) if bbox else img


def artwork_scale(w: int, h: int, edge: int) -> float:
    """STEP 5: the smaller of (EDGE/W) and (EDGE/H), but NEVER above 1.0.

    That cap is the whole safety rule in one line. A scale factor above 1.0 means
    enlarging, and enlarging a logo blurs it. There is no case in which we want
    that, so the cap is applied here and nowhere else can bypass it.
    """
    return min(edge / w, edge / h, 1.0)


def artwork_box(w: int, h: int, edge: int) -> tuple[int, int, int, int]:
    """Where the artwork lands on the canvas: (left, top, width, height)."""
    scale = artwork_scale(w, h, edge)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    return (edge - nw) // 2, (edge - nh) // 2, nw, nh


def square_canvas(img: Image.Image, edge: int, bg_hex: str) -> Image.Image:
    """STEP 4 + STEP 5: pad onto a square. Never crop, never stretch.

    One scale factor is applied to both axes, so the artwork cannot be squashed;
    the canvas is always at least as large as the scaled artwork, so nothing can
    be cut off. Transparency is flattened onto the chosen colour: left in place,
    the finished square would show whatever sits behind it in the advert rather
    than the colour we picked.
    """
    rgb = hex_to_rgb(bg_hex)
    w, h = img.size
    left, top, nw, nh = artwork_box(w, h, edge)
    art = img.resize((nw, nh), Image.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", (edge, edge), rgb + (255,))
    canvas.alpha_composite(art, (left, top))
    return canvas.convert("RGB")          # flattened: no alpha survives to Google


def render_square(img: Image.Image, edge: int, bg_hex: str) -> Image.Image:
    source = trim_border(img, hex_to_rgb(bg_hex)) if settings.TRIM_BORDER else img
    return square_canvas(source, edge, bg_hex)


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
        timeout=settings.UPSTREAM_TIMEOUT,
        max_retries=settings.MODEL_MAX_RETRIES,
    )


def build_prompt(ctx: dict) -> str:
    try:
        template = settings.PROMPT_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise LogoError(f"Prompt file unreadable: {exc}") from exc
    return template.format(**ctx)


def _work_size(edge: int) -> int:
    """The raster the model is asked for.

    gpt-image emits fixed sizes, so the model cannot natively produce an
    arbitrary EDGE between 128 and 1200. It renders at IMAGE_WORK_SIZE and the
    result is resampled down to EDGE. Where EDGE is larger than the model's
    raster the artwork is not taken from that render at all - composite_source
    puts the source pixels back at full resolution - so nothing is ever enlarged.
    """
    return int(settings.IMAGE_WORK_SIZE.split("x")[0])


def composite_source(canvas: Image.Image, source: Image.Image,
                     edge: int, bg_hex: str) -> Image.Image:
    """Paste the source artwork back over its own rectangle.

    The model renders the square; this puts the business's own pixels back on top
    of the area the artwork occupies, scaled by exactly the factor STEP 5 allows
    and taken from the source at full resolution. After this the artwork is the
    source - not a likeness of it - so "the logo itself is untouched" is a
    property of the code rather than something the prompt has to win.
    """
    left, top, nw, nh = artwork_box(*source.size, edge)
    art = source.resize((nw, nh), Image.LANCZOS).convert("RGBA")
    # Blend the artwork's soft edges against the plate colour that was asked
    # for, not against whatever the model happened to draw underneath. Pasting
    # straight onto the render would tint every anti-aliased pixel with the
    # model's background, so the artwork would carry the model's colour at its
    # edges even though the middle was replaced.
    block = Image.new("RGBA", (nw, nh), hex_to_rgb(bg_hex) + (255,))
    block.alpha_composite(art)
    out = canvas.convert("RGB")
    out.paste(block.convert("RGB"), (left, top))
    return out


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


def _as_upstream(exc: Exception) -> UpstreamRejected:
    """Map a transport or API failure onto one of the rejection codes.

    A timeout and a refused connection are the same thing to the caller - the
    renderer did not answer in time - so both carry
    upstream_unreachable_or_timeout; anything the API reported itself is
    upstream_error.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    unreachable = ("timeout", "timedout", "connect", "connection", "unreachable",
                   "network", "socket", "ssl")
    if any(t in name for t in unreachable) or any(t in text for t in unreachable):
        return UpstreamRejected("upstream_unreachable_or_timeout", str(exc))
    return UpstreamRejected("upstream_error", str(exc))


def render_ai(source: Image.Image, edge: int, bg_hex: str, meta: dict) -> Image.Image:
    """Ask the model for the square, then hand back its EDGE x EDGE raster.

    The seed is the square already composed at the model's working size, so the
    model is given a correct composition to reproduce rather than a resizing job
    to perform - it is never asked to scale or centre anything, because that has
    already been done. The prompt is told the real canvas size and the real hex
    plate colour, both of which vary per logo under the sizing rule.
    """
    work = _work_size(edge)
    seed = square_canvas(source, work, bg_hex)
    seed_png = to_png_bytes(seed)
    left, top, content_w, content_h = artwork_box(*source.size, work)
    prompt = build_prompt(
        {
            "canvas_size": work,
            "edge": edge,
            "source_width": meta["source_width"],
            "source_height": meta["source_height"],
            "aspect_type": meta["aspect_type"],
            "content_width": content_w,
            "content_height": content_h,
            "content_left": left,
            "content_top": top,
            "background_hex": bg_hex,
        }
    )

    base = {
        "model": settings.IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "size": settings.IMAGE_WORK_SIZE,
    }
    # The plate is always opaque now: the rules require transparency to be
    # flattened onto the chosen colour before the image ever leaves here.
    optional = {
        "quality": settings.IMAGE_QUALITY,
        "input_fidelity": settings.IMAGE_INPUT_FIDELITY,
        "output_format": "png",
        "background": "opaque",
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
                raise _as_upstream(exc) from exc
            optional.pop(key)
            dropped.append(key)
    if result is None:
        raise UpstreamRejected("upstream_error",
                               "The renderer rejected every parameter combination.")
    if dropped:
        meta["model_params_dropped"] = dropped

    item = result.data[0]
    if getattr(item, "b64_json", None):
        raw = base64.b64decode(item.b64_json)
    elif getattr(item, "url", None):
        raw = fetch_url(item.url)
    else:
        raise UpstreamRejected("upstream_missing_output_url",
                               "The renderer returned no image link.")
    render = open_image(raw)
    # Resample the model's fixed raster to the EDGE the arithmetic asked for.
    if render.size != (edge, edge):
        render = render.convert("RGB").resize((edge, edge), Image.LANCZOS)
    return render.convert("RGB")


# ------------------------------------------------------------------ validation

def validate_square(img: Image.Image, expected_edge: int, png: bytes) -> None:
    """The arrival checks, in the order and with the names the rules give.

    We can check what comes back; we cannot check whether the renderer got there
    honestly, which is why the size was computed here and sent as an instruction.
    A rejection is never visible to the business - the caller squares the logo
    in-house and the advert is built as normal.
    """
    w, h = img.size
    if w != h:
        raise UpstreamRejected("not_square", f"Returned {w} x {h}, which is not square.")
    if w < settings.EDGE_MIN or w > settings.EDGE_MAX:
        raise UpstreamRejected(
            "out_of_range",
            f"Returned {w} px, outside {settings.EDGE_MIN}-{settings.EDGE_MAX}.")
    if w != expected_edge:
        raise UpstreamRejected(
            "out_of_range",
            f"Returned {w} px, but {expected_edge} px was asked for.")
    limit = settings.MAX_OUTPUT_MB * 1024 * 1024
    if len(png) > limit:
        raise UpstreamRejected(
            "out_of_range",
            f"Returned {len(png)/1024/1024:.1f} MB, over the "
            f"{settings.MAX_OUTPUT_MB:.0f} MB limit.")


# ------------------------------------------------------------------ provenance

def build_provenance(source_ref: str, renderer: str, edge: int) -> dict:
    """How the square must be filed: machine-made, and a copy of the original.

    The rules are explicit that "the square version is labelled as machine-made
    and filed as a copy of the original, never passed off as the business's own
    upload". This is the marker the platform files it by; the platform owns the
    asset library, so all this service can do is state plainly what the file is
    and what it came from.

    The rules say what the label must convey but do not name the fields, so the
    three keys that carry meaning - machine_made, copy_of, business_upload - are
    this service's naming and need confirming against the asset library.
    """
    return {
        "machine_made": True,
        "business_upload": False,
        "copy_of": source_ref or None,
        "produced_by": f"{settings.APP_NAME} {settings.APP_VERSION}",
        "method": f"squared to {edge}x{edge} by {renderer}",
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ----------------------------------------------------------------- entry point

def square_logo(data: bytes, filename: str = "logo.png", mode=None,
                save=None, source_name: str = "",
                size: int | None = None, background: str | None = None) -> dict:
    """The five checks, in order, and what comes out.

    Check 1  can the file be read at all      -> UnreadableLogo if not
    Check 2  is it already square             -> to within SQUARE_TOLERANCE
    Check 3  how big is the square going to be
    Check 4  which background colour
    Check 5  make it

    There is one procedure, not two: a square logo and a horizontal one run the
    same path, because checks 3 to 5 give a square logo the right answer anyway.
    Only an unreadable file is refused - there is no size at which we refuse.
    """
    started = time.perf_counter()
    mode = (mode or settings.DEFAULT_MODE).lower()
    if mode not in MODES:
        raise LogoError(f"mode must be one of {', '.join(MODES)}")

    # ---- check 1: can the file be read at all?
    src = open_image(data)
    w, h = src.size

    # ---- check 2: is it already square? (informational: the path is the same)
    aspect = classify_aspect(w, h)

    # ---- check 3: how big is the square going to be?
    if size is None:
        edge, edge_source = compute_edge(w, h), "computed from the longer side"
    else:
        edge = int(size)
        if edge < settings.EDGE_MIN or edge > settings.EDGE_MAX:
            raise LogoError(
                f"size must be between {settings.EDGE_MIN} and {settings.EDGE_MAX}")
        edge_source = "supplied by the caller"
    scale = artwork_scale(w, h, edge)

    # ---- check 4: which background colour?
    if background is None:
        bg_hex, bg_reason = choose_background(src, source_name or filename)
    else:
        bg_hex = rgb_to_hex(hex_to_rgb(background))   # validates and normalises
        bg_reason = "supplied by the caller"

    meta = {
        "filename": filename,
        "source_width": w,
        "source_height": h,
        "source_format": (src.format or "UNKNOWN").upper(),
        "aspect_type": aspect,
        "source_transparent": has_transparency(src),
        "edge": edge,
        "edge_source": edge_source,
        "scale_factor": round(scale, 4),
        "enlarged": scale > 1.0,          # always False: STEP 5 caps at 1.0
        "artwork_size": list(artwork_box(w, h, edge)[2:]),
        "background_hex": bg_hex,
        "background_reason": bg_reason,
    }

    # ---- check 5: make it
    reference = render_square(src, edge, bg_hex)
    final, metrics, used = reference, None, "exact"
    rejection, note = None, None

    if mode in ("ai", "hybrid"):
        try:
            render = render_ai(src, edge, bg_hex, meta)
            png_try = to_png_bytes(render)
            validate_square(render, edge, png_try)
            if settings.COMPOSITE_SOURCE:
                render = composite_source(render, src, edge, bg_hex)
            metrics = compare(reference, render)
            final, used = render, "ai"
        except UpstreamRejected as exc:
            rejection = exc.code
            note = f"{exc.detail or exc.code}. Squared in-house instead."
            if mode == "ai":
                raise
        except LogoError as exc:
            rejection = "upstream_error"
            note = f"{exc}. Squared in-house instead."
            if mode == "ai":
                raise

    provenance = build_provenance(source_name or filename, used, edge)
    out_name = f"{Path(filename).stem or 'logo'}_{edge}x{edge}_{uuid.uuid4().hex[:8]}.png"
    png = to_png_bytes(final, provenance)
    out_path = None
    if settings.SAVE_OUTPUT if save is None else save:
        out_path = settings.OUTPUT_DIR / out_name
        out_path.write_bytes(png)

    return {
        "status": "ok",
        "mode_requested": mode,
        "renderer_used": used,
        "fell_back_in_house": rejection is not None,
        "rejected_as": rejection,
        "composited_source": bool(settings.COMPOSITE_SOURCE and used == "ai"),
        "note": note,
        "provenance": provenance,
        "source": meta,
        "request_sent": {
            "size": edge,
            "background": bg_hex,
        },
        "output": {
            "width": final.size[0],
            "height": final.size[1],
            "format": "PNG",
            "filename": out_name,
            "path": str(out_path) if out_path else None,
            "bytes": len(png),
            "within_5mb": len(png) <= settings.MAX_OUTPUT_MB * 1024 * 1024,
        },
        "fidelity": metrics,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "image_base64": base64.b64encode(png).decode(),
    }


# The endpoint's own name for the operation; `process` is kept as an alias so
# existing callers (scripts, the controller) keep working.
process = square_logo
