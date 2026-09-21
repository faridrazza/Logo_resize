"""Central configuration. Every tunable lives here and is driven by .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _str(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key) or default)
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key) or default)
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    raw = _str(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


class Settings:
    # --- server ---
    APP_NAME = _str("APP_NAME", "Logo Resize Service")
    APP_VERSION = _str("APP_VERSION", "1.0.0")
    HOST = _str("HOST", "0.0.0.0")
    PORT = _int("PORT", 8000)
    API_PREFIX = _str("API_PREFIX", "/api/v1")
    CORS_ORIGINS = [o.strip() for o in _str("CORS_ORIGINS", "*").split(",") if o.strip()]

    # --- output geometry: Logo Squaring Rules, 2026-09-19 ---
    # The square's edge is derived per logo, never fixed:
    #   EDGE = clamp(max(width, height), EDGE_MIN, EDGE_MAX)
    # EDGE_MIN is Google's floor for the *image*; a logo smaller than that is
    # centred on a 128 canvas rather than refused or enlarged. EDGE_MAX is the
    # size Google recommends; nothing above it helps.
    EDGE_MIN = _int("EDGE_MIN", 128)
    EDGE_MAX = _int("EDGE_MAX", 1200)
    # "Square to within about 5 percent, not to the pixel" - a 1000x1010 logo is
    # square for our purposes and must not be pushed down the horizontal path.
    SQUARE_TOLERANCE = _float("SQUARE_TOLERANCE", 0.05)
    # The two background colours in use. No brand-colour matching: guessing a
    # brand's own colour and getting it slightly wrong looks worse than white.
    BG_LIGHT = _str("BG_LIGHT", "#ffffff")
    BG_DARK = _str("BG_DARK", "#0d0d0d")
    # Google's file ceiling for the logo asset.
    MAX_OUTPUT_MB = _float("MAX_OUTPUT_MB", 5.0)
    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 25)
    # Trim a uniform border before fitting. OFF by default: never touch framing.
    TRIM_BORDER = _bool("TRIM_BORDER", False)
    TRIM_TOLERANCE = _int("TRIM_TOLERANCE", 8)

    # --- processing mode: exact | ai | hybrid ---
    DEFAULT_MODE = _str("DEFAULT_MODE", "hybrid").lower()

    # --- image model ---
    OPENAI_API_KEY = _str("OPENAI_API_KEY")
    OPENAI_BASE_URL = _str("OPENAI_BASE_URL")
    IMAGE_MODEL = _str("IMAGE_MODEL", "gpt-image-2.5-flare")
    IMAGE_WORK_SIZE = _str("IMAGE_WORK_SIZE", "1024x1024")
    IMAGE_QUALITY = _str("IMAGE_QUALITY", "high")
    IMAGE_INPUT_FIDELITY = _str("IMAGE_INPUT_FIDELITY", "high")
    # The contract allows the renderer 30 seconds; past that it is treated as
    # upstream_unreachable_or_timeout and the logo is squared in-house.
    UPSTREAM_TIMEOUT = _float("UPSTREAM_TIMEOUT", 30.0)
    # Plain downloads are not the upstream renderer and keep their own budget.
    REQUEST_TIMEOUT = _float("REQUEST_TIMEOUT", 45.0)
    MODEL_MAX_RETRIES = _int("MODEL_MAX_RETRIES", 3)
    PROMPT_FILE = BASE_DIR / _str("PROMPT_FILE", "prompt.txt")
    # Paste the source artwork back over its own rectangle after the model has
    # rendered the square. The artwork is then the source, pixel for pixel, and
    # "no changes to the logo" is a property of the code rather than a hope.
    COMPOSITE_SOURCE = _bool("COMPOSITE_SOURCE", True)

    # --- fidelity gate (hybrid falls back to the exact result when these fail) ---
    VERIFY_PHASH_MAX = _int("VERIFY_PHASH_MAX", 6)
    VERIFY_SSIM_MIN = _float("VERIFY_SSIM_MIN", 0.93)
    VERIFY_PIXEL_DIFF_MAX = _float("VERIFY_PIXEL_DIFF_MAX", 0.04)
    VERIFY_COLOR_DELTA_MAX = _float("VERIFY_COLOR_DELTA_MAX", 12.0)

    # --- storage ---
    INPUT_DIR = BASE_DIR / _str("INPUT_DIR", "data/input")
    OUTPUT_DIR = BASE_DIR / _str("OUTPUT_DIR", "data/output")
    SAVE_OUTPUT = _bool("SAVE_OUTPUT", True)


settings = Settings()
settings.INPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
