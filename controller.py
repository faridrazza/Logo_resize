"""Controller: validates input, calls the service, shapes the HTTP response."""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi import HTTPException, UploadFile
from fastapi.responses import Response

import service
from config import settings


async def _read_source(file: UploadFile | None, image_url: str | None) -> tuple[bytes, str, str]:
    """Return (bytes, filename, source name for the background hint).

    There is deliberately no check on file extension here. The rules refuse a
    logo for one reason only - the file cannot be read - and that is decided by
    actually opening it in check 1, not by guessing from its name.
    """
    if not file and not image_url:
        raise HTTPException(400, "Provide either a 'file' upload or an 'image_url'.")
    if file and image_url:
        raise HTTPException(400, "Provide only one of 'file' or 'image_url'.")

    if file:
        name = file.filename or "logo.png"
        data = await file.read()
        if not data:
            raise HTTPException(400, "Uploaded file is empty.")
        if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"File exceeds {settings.MAX_UPLOAD_MB} MB.")
        return data, name, name

    try:
        data = service.fetch_url(image_url)
    except service.LogoError as exc:
        raise HTTPException(400, str(exc)) from exc
    name = Path(image_url.split("?")[0]).name or "logo.png"
    # The hint comes from the URL's own filename: a designer's "logo-on-dark"
    # survives there, where a locally renamed copy would have lost it.
    return data, name, image_url


def _run(data: bytes, name: str, source_name: str, size, background, mode) -> dict:
    try:
        return service.square_logo(
            data, filename=name, mode=mode, source_name=source_name,
            size=size, background=background,
        )
    except service.UnreadableLogo as exc:
        raise HTTPException(422, str(exc)) from exc
    except service.LogoError as exc:
        raise HTTPException(422, str(exc)) from exc


async def square(payload) -> dict:
    """POST /logo/square - the endpoint contract: a URL, a size, a hex colour."""
    try:
        data = service.fetch_url(payload.logo_url)
    except service.LogoError as exc:
        raise HTTPException(400, str(exc)) from exc
    name = Path(payload.logo_url.split("?")[0]).name or "logo.png"
    return _run(data, name, payload.logo_url, payload.size, payload.background, payload.mode)


async def resize(file, image_url, size, background, mode, return_image):
    """POST /logo/resize - one logo in, one square PNG out."""
    data, name, source_name = await _read_source(file, image_url)
    result = _run(data, name, source_name, size, background, mode)

    if return_image:
        return Response(
            content=base64.b64decode(result["image_base64"]),
            media_type="image/png",
            headers={
                "Content-Disposition": f'inline; filename="{result["output"]["filename"]}"',
                "X-Logo-Edge": str(result["source"]["edge"]),
                "X-Logo-Renderer": result["renderer_used"],
                "X-Logo-Background": result["source"]["background_hex"],
                # The square must never be filed as the business's own upload,
                # so the label travels on the streamed response too.
                "X-Logo-Machine-Made": "true",
                "X-Logo-Copy-Of": str(result["provenance"].get("copy_of") or ""),
            },
        )
    return result


async def resize_batch(payload):
    """POST /logo/batch - run a list of URLs or local paths in one call."""
    items = payload.items
    if not items:
        raise HTTPException(400, "'items' must contain at least one entry.")
    if len(items) > 200:
        raise HTTPException(400, "Batch limit is 200 items per request.")

    rows, ok, fell_back, refused = [], 0, 0, 0
    for index, item in enumerate(items, start=1):
        source = item.strip()
        try:
            if source.lower().startswith(("http://", "https://")):
                data = service.fetch_url(source)
                name, source_name = Path(source.split("?")[0]).name, source
            else:
                path = Path(source)
                if not path.is_absolute():
                    path = settings.INPUT_DIR / source
                data, name, source_name = path.read_bytes(), path.name, path.name
            result = service.square_logo(
                data, filename=name or "logo.png", mode=payload.mode,
                source_name=source_name,
            )
            result.pop("image_base64", None)
            result["index"], result["source_ref"] = index, source
            rows.append(result)
            ok += 1
            if result["fell_back_in_house"]:
                fell_back += 1
        except (service.LogoError, OSError) as exc:
            refused += 1
            code = getattr(exc, "code", "unreadable")
            rows.append({"index": index, "source_ref": source, "status": "refused",
                         "reason": code, "error": str(exc)})

    return {
        "status": "ok",
        "summary": {
            "total": len(items),
            "squared": ok,
            "fell_back_in_house": fell_back,
            "refused_unreadable": refused,
            "output_dir": str(settings.OUTPUT_DIR),
        },
        "results": rows,
    }


def health():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "edge_range": f"{settings.EDGE_MIN}-{settings.EDGE_MAX}",
        "square_tolerance": settings.SQUARE_TOLERANCE,
        "backgrounds": {"light": settings.BG_LIGHT, "dark": settings.BG_DARK},
        "default_mode": settings.DEFAULT_MODE,
        "image_model": settings.IMAGE_MODEL,
        "composite_source": settings.COMPOSITE_SOURCE,
        "upstream_timeout_s": settings.UPSTREAM_TIMEOUT,
        "model_key_configured": bool(settings.OPENAI_API_KEY),
    }
