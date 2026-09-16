"""Single router: all logo endpoints."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

import controller
from config import settings

router = APIRouter(prefix=f"{settings.API_PREFIX}/logo", tags=["logo"])


class BatchRequest(BaseModel):
    items: list[str] = Field(
        ...,
        description="Image URLs, or filenames/paths relative to INPUT_DIR.",
        examples=[["https://example.com/a.png", "acme.png"]],
    )
    mode: str | None = Field(
        None, description="exact | ai | hybrid (defaults to DEFAULT_MODE)"
    )


@router.get("/health", summary="Service health and active configuration")
def health():
    return controller.health()


@router.post("/resize", summary="Resize one logo to 300x300 without altering it")
async def resize(
    file: UploadFile | None = File(None, description="Logo image upload"),
    image_url: str | None = Form(None, description="Public URL of the logo"),
    mode: str | None = Form(None, description="exact | ai | hybrid"),
    return_image: bool = Form(False, description="true -> stream PNG instead of JSON"),
):
    return await controller.resize(file, image_url, mode, return_image)


@router.post("/batch", summary="Resize many logos and report fidelity for each")
async def batch(payload: BatchRequest):
    return await controller.resize_batch(payload)
