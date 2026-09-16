"""Fidelity checks: does the produced logo still look exactly like the source?

All metrics compare the candidate against the pixel-exact deterministic render,
so a "pass" means the model changed nothing a human would notice.
Pure numpy + Pillow, no heavy CV dependency.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
from PIL import Image

from config import settings


def _on_white(img: Image.Image) -> Image.Image:
    """Flatten onto white so transparent and opaque renders are comparable."""
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(bg, img).convert("RGB")


def _gray(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("L"), dtype=np.float32)


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n, dtype=np.float32)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    m[0] *= 1.0 / np.sqrt(2.0)
    return m * np.sqrt(2.0 / n)


def phash(img: Image.Image, hash_size: int = 8, factor: int = 4) -> np.ndarray:
    """Perceptual hash (DCT based). Structure-sensitive, scale-insensitive."""
    n = hash_size * factor
    small = img.convert("L").resize((n, n), Image.LANCZOS)
    d = _dct_matrix(n)
    coeffs = d @ np.asarray(small, dtype=np.float32) @ d.T
    block = coeffs[:hash_size, :hash_size].flatten()
    median = np.median(block[1:])  # drop DC term
    return block > median


def phash_distance(a: Image.Image, b: Image.Image) -> int:
    return int(np.count_nonzero(phash(a) != phash(b)))


def _box_mean(x: np.ndarray, w: int = 7) -> np.ndarray:
    k = np.ones(w, dtype=np.float32) / w
    rows = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, x)
    return np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, rows)


def ssim(a: Image.Image, b: Image.Image, win: int = 7) -> float:
    """Local structural similarity on the luminance channel."""
    x, y = _gray(a), _gray(b)
    if x.shape != y.shape:
        y = _gray(b.resize(a.size, Image.LANCZOS))
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = _box_mean(x, win), _box_mean(y, win)
    mxx, myy = _box_mean(x * x, win), _box_mean(y * y, win)
    mxy = _box_mean(x * y, win)
    vx, vy = mxx - mx * mx, myy - my * my
    vxy = mxy - mx * my
    num = (2 * mx * my + c1) * (2 * vxy + c2)
    den = (mx * mx + my * my + c1) * (vx + vy + c2)
    return float(np.mean(num / np.maximum(den, 1e-8)))


def mean_pixel_diff(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute RGB difference, normalised 0..1."""
    x = np.asarray(_on_white(a), dtype=np.float32)
    y = np.asarray(_on_white(b), dtype=np.float32)
    if x.shape != y.shape:
        y = np.asarray(_on_white(b.resize(a.size, Image.LANCZOS)), dtype=np.float32)
    return float(np.mean(np.abs(x - y)) / 255.0)


def _palette(img: Image.Image, top: int = 6) -> list[tuple[int, int, int]]:
    """Dominant colours, quantised to 16-step buckets, background excluded."""
    px = np.asarray(_on_white(img).resize((120, 120), Image.NEAREST), dtype=np.uint8)
    buckets = (px // 16 * 16).reshape(-1, 3)
    counts = Counter(map(tuple, buckets))
    counts.pop((240, 240, 240), None)
    return [c for c, _ in counts.most_common(top)]


def color_delta(a: Image.Image, b: Image.Image) -> float:
    """Worst nearest-neighbour distance between the two dominant palettes."""
    pa, pb = _palette(a), _palette(b)
    if not pa or not pb:
        return 0.0
    arr_b = np.array(pb, dtype=np.float32)
    worst = 0.0
    for c in pa:
        d = float(np.min(np.linalg.norm(arr_b - np.array(c, dtype=np.float32), axis=1)))
        worst = max(worst, d)
    return worst


def compare(reference: Image.Image, candidate: Image.Image) -> dict:
    """Full fidelity report of `candidate` against the pixel-exact `reference`."""
    ref, cand = _on_white(reference), _on_white(candidate)
    metrics = {
        "phash_distance": phash_distance(ref, cand),
        "ssim": round(ssim(ref, cand), 4),
        "mean_pixel_diff": round(mean_pixel_diff(ref, cand), 4),
        "color_delta": round(color_delta(ref, cand), 2),
    }
    reasons = []
    if metrics["phash_distance"] > settings.VERIFY_PHASH_MAX:
        reasons.append(f"phash {metrics['phash_distance']} > {settings.VERIFY_PHASH_MAX}")
    if metrics["ssim"] < settings.VERIFY_SSIM_MIN:
        reasons.append(f"ssim {metrics['ssim']} < {settings.VERIFY_SSIM_MIN}")
    if metrics["mean_pixel_diff"] > settings.VERIFY_PIXEL_DIFF_MAX:
        reasons.append(f"pixel_diff {metrics['mean_pixel_diff']} > {settings.VERIFY_PIXEL_DIFF_MAX}")
    if metrics["color_delta"] > settings.VERIFY_COLOR_DELTA_MAX:
        reasons.append(f"color_delta {metrics['color_delta']} > {settings.VERIFY_COLOR_DELTA_MAX}")
    metrics["passed"] = not reasons
    metrics["reasons"] = reasons
    return metrics
