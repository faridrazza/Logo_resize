"""Single router: all logo endpoints."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field

import controller
from config import settings

router = APIRouter(prefix=f"{settings.API_PREFIX}/logo", tags=["logo"])


class SquareRequest(BaseModel):
    """The send side of the contract.

    size and background are optional: when they are omitted the service works
    them out with the sizing rule and reports what it used. When the caller
    supplies them it is supplying the arithmetic it already did, and the service
    honours it - that is the point of sending the size as an instruction rather
    than asking the renderer to choose.
    """

    logo_url: str = Field(..., description="A link the endpoint can fetch")
    size: int | None = Field(
        None, ge=128, le=1200,
        description="The finished square's width and height, 128 to 1200",
    )
    background: str | None = Field(
        None, description="A hex colour, e.g. #ffffff", examples=["#ffffff"]
    )
    mode: str | None = Field(None, description="exact | ai | hybrid")


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


@router.post("/square", summary="Square a logo by URL (the endpoint contract)")
async def square(payload: SquareRequest):
    return await controller.square(payload)


@router.post("/resize", summary="Square an uploaded logo onto its own EDGE canvas")
async def resize(
    file: UploadFile | None = File(None, description="Logo image upload"),
    image_url: str | None = Form(None, description="Public URL of the logo"),
    size: int | None = Form(None, description="Override the computed square size"),
    background: str | None = Form(None, description="Override the plate, e.g. #ffffff"),
    mode: str | None = Form(None, description="exact | ai | hybrid"),
    return_image: bool = Form(False, description="true -> stream PNG instead of JSON"),
):
    return await controller.resize(file, image_url, size, background, mode, return_image)


@router.post("/batch", summary="Square many logos and report each one")
async def batch(payload: BatchRequest):
    return await controller.resize_batch(payload)
