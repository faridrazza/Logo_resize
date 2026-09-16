"""Controller: validates input, calls the service, shapes the HTTP response."""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi import HTTPException, UploadFile
from fastapi.responses import Response

import service
from config import settings

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


async def _read_source(file: UploadFile | None, image_url: str | None) -> tuple[bytes, str]:
    if not file and not image_url:
        raise HTTPException(400, "Provide either a 'file' upload or an 'image_url'.")
    if file and image_url:
        raise HTTPException(400, "Provide only one of 'file' or 'image_url'.")

    if file:
        name = file.filename or "logo.png"
        if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise HTTPException(415, f"Unsupported file type. Allowed: {sorted(ALLOWED_SUFFIXES)}")
        data = await file.read()
        if not data:
            raise HTTPException(400, "Uploaded file is empty.")
        if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"File exceeds {settings.MAX_UPLOAD_MB} MB.")
        return data, name

    try:
        data = service.fetch_url(image_url)
    except service.LogoError as exc:
        raise HTTPException(400, str(exc)) from exc
    name = Path(image_url.split("?")[0]).name or "logo.png"
    return data, name


async def resize(file, image_url, mode, return_image):
    """POST /logo/resize - one logo in, a 300x300 PNG out."""
    data, name = await _read_source(file, image_url)
    try:
        result = service.process(data, filename=name, mode=mode)
    except service.LogoError as exc:
        raise HTTPException(422, str(exc)) from exc

    if return_image:
        return Response(
            content=base64.b64decode(result["image_base64"]),
            media_type="image/png",
            headers={
                "Content-Disposition": f'inline; filename="{result["output"]["filename"]}"',
                "X-Logo-Verdict": result["verdict"],
                "X-Logo-Renderer": result["renderer_used"],
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

    rows, ok, review, failed = [], 0, 0, 0
    for index, item in enumerate(items, start=1):
        source = item.strip()
        try:
            if source.lower().startswith(("http://", "https://")):
                data, name = service.fetch_url(source), Path(source.split("?")[0]).name
            else:
                path = Path(source)
                if not path.is_absolute():
                    path = settings.INPUT_DIR / source
                data, name = path.read_bytes(), path.name
            result = service.process(data, filename=name or "logo.png", mode=payload.mode)
            result.pop("image_base64", None)
            result["index"], result["source_ref"] = index, source
            rows.append(result)
            if result["verdict"] == "REVIEW":
                review += 1
            else:
                ok += 1
        except (service.LogoError, OSError) as exc:
            failed += 1
            rows.append(
                {"index": index, "source_ref": source, "status": "error", "error": str(exc)}
            )

    return {
        "status": "ok",
        "summary": {
            "total": len(items),
            "unchanged": ok,
            "needs_review": review,
            "failed": failed,
            "output_dir": str(settings.OUTPUT_DIR),
        },
        "results": rows,
    }


def health():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "target_size": f"{settings.TARGET_SIZE}x{settings.TARGET_SIZE}",
        "default_mode": settings.DEFAULT_MODE,
        "image_model": settings.IMAGE_MODEL,
        "model_key_configured": bool(settings.OPENAI_API_KEY),
    }
