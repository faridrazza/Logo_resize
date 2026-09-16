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

    # --- output geometry ---
    TARGET_SIZE = _int("TARGET_SIZE", 300)
    # auto | transparent | white  -> how the padding around the logo is filled
    OUTPUT_BACKGROUND = _str("OUTPUT_BACKGROUND", "auto").lower()
    ALLOW_UPSCALE = _bool("ALLOW_UPSCALE", True)
    # Trim a uniform border before fitting. OFF by default: never touch framing.
    TRIM_BORDER = _bool("TRIM_BORDER", False)
    TRIM_TOLERANCE = _int("TRIM_TOLERANCE", 8)
    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 25)

    # --- processing mode: exact | ai | hybrid ---
    DEFAULT_MODE = _str("DEFAULT_MODE", "hybrid").lower()

    # --- image model ---
    OPENAI_API_KEY = _str("OPENAI_API_KEY")
    OPENAI_BASE_URL = _str("OPENAI_BASE_URL")
    IMAGE_MODEL = _str("IMAGE_MODEL", "gpt-image-2.5-flare")
    IMAGE_WORK_SIZE = _str("IMAGE_WORK_SIZE", "1024x1024")
    IMAGE_QUALITY = _str("IMAGE_QUALITY", "high")
    IMAGE_INPUT_FIDELITY = _str("IMAGE_INPUT_FIDELITY", "high")
    REQUEST_TIMEOUT = _float("REQUEST_TIMEOUT", 180.0)
    MODEL_MAX_RETRIES = _int("MODEL_MAX_RETRIES", 3)
    PROMPT_FILE = BASE_DIR / _str("PROMPT_FILE", "prompt.txt")

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
