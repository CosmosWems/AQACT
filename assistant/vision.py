"""Offline chart-image inspection.

The assistant cannot send images to a remote vision model.  Instead, this
module extracts objective visual properties (size, background, contrast,
colourfulness, edge density, dominant palette, estimated plot area) and turns
them into a careful description.  It never claims to read exact axis values
that are not computationally legible.
"""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np
from PIL import Image, ImageOps


def _load_image(source: Union[str, Path, bytes, bytearray]) -> Image.Image:
    if isinstance(source, (bytes, bytearray)):
        image = Image.open(io.BytesIO(bytes(source)))
    elif isinstance(source, str) and source.startswith("data:"):
        payload = source.split(",", 1)[-1]
        image = Image.open(io.BytesIO(base64.b64decode(payload)))
    else:
        image = Image.open(source)
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*[max(0, min(255, int(v))) for v in rgb])


def _dominant_palette(array: np.ndarray, limit: int = 6) -> list[dict]:
    """Cluster colours by coarse quantisation, then return shares."""
    sample = array.reshape(-1, 3)
    if sample.shape[0] > 40000:
        step = max(1, sample.shape[0] // 40000)
        sample = sample[::step]
    quantised = (sample // 24) * 24
    colours, counts = np.unique(quantised, axis=0, return_counts=True)
    order = np.argsort(counts)[::-1]
    total = max(1, counts.sum())
    palette = []
    for index in order[:limit]:
        rgb = tuple(int(v) for v in colours[index])
        palette.append({
            "hex": _rgb_to_hex(rgb),
            "rgb": list(rgb),
            "share_pct": round(float(counts[index]) / total * 100, 2),
        })
    return palette


def describe_chart_image(source: Union[str, Path, bytes, bytearray]) -> dict:
    """Return a deterministic visual description of an uploaded chart image."""
    image = _load_image(source)
    width, height = image.size
    aspect = width / height if height else 1.0

    # Downscale for fast, memory-bounded analysis.
    working = image.copy()
    working.thumbnail((900, 900))
    array = np.asarray(working, dtype=np.float32)

    gray = array.mean(axis=2)
    brightness = float(gray.mean())
    contrast = float(gray.std())

    # Background estimated from the image border.
    border = np.concatenate([
        array[0, :, :], array[-1, :, :], array[:, 0, :], array[:, -1, :],
    ], axis=0)
    background = np.median(border, axis=0)
    background_luma = float(background.mean())
    dark_background = background_luma < 110

    distance = np.linalg.norm(array - background, axis=2)
    background_mask = distance < 22
    whitespace = float(background_mask.mean() * 100)

    # Edge density from a simple central-difference gradient.
    gy = np.abs(np.diff(gray, axis=0)).mean() if gray.shape[0] > 1 else 0.0
    gx = np.abs(np.diff(gray, axis=1)).mean() if gray.shape[1] > 1 else 0.0
    edge_density = float((gx + gy) / 2)

    max_channel = array.max(axis=2)
    min_channel = array.min(axis=2)
    saturation = (max_channel - min_channel) / np.maximum(max_channel, 1)
    colourfulness = float(saturation.mean() * 100)

    foreground = ~background_mask
    plot_region = None
    if foreground.any():
        ys, xs = np.where(foreground)
        plot_region = {
            "left_pct": round(float(xs.min()) / max(1, array.shape[1]) * 100, 1),
            "right_pct": round(float(xs.max()) / max(1, array.shape[1]) * 100, 1),
            "top_pct": round(float(ys.min()) / max(1, array.shape[0]) * 100, 1),
            "bottom_pct": round(float(ys.max()) / max(1, array.shape[0]) * 100, 1),
        }

    palette = _dominant_palette(array)
    chromatic = [p for p in palette if max(p["rgb"]) - min(p["rgb"]) > 30]

    observations: list[str] = []
    observations.append(
        f"The image is {width}×{height} px ({aspect:.2f}:1), so it is "
        + ("portrait" if aspect < 0.9 else "close to square" if aspect < 1.15 else "landscape" if aspect < 2.2 else "very wide")
        + "."
    )
    observations.append(
        f"It uses a {'dark' if dark_background else 'light'} background "
        f"(estimated {_rgb_to_hex(tuple(background))}) with about {whitespace:.1f}% empty space."
    )
    observations.append(
        "Contrast is "
        + ("high" if contrast > 60 else "moderate" if contrast > 32 else "low")
        + f" (grayscale SD {contrast:.1f}) and visual detail is "
        + ("dense" if edge_density > 22 else "moderate" if edge_density > 10 else "sparse")
        + f" (edge score {edge_density:.1f})."
    )
    observations.append(
        "The palette is "
        + ("vivid" if colourfulness > 28 else "moderately colourful" if colourfulness > 12 else "muted")
        + f" ({colourfulness:.1f}% mean saturation)."
    )
    if chromatic:
        observations.append("Prominent series colours: " + ", ".join(p["hex"] for p in chromatic[:5]) + ".")
    if plot_region:
        observations.append(
            f"Non-background content spans roughly {plot_region['left_pct']}–{plot_region['right_pct']}% "
            f"horizontally and {plot_region['top_pct']}–{plot_region['bottom_pct']}% vertically."
        )

    chart_likelihood = 0.0
    chart_likelihood += 0.25 if whitespace > 40 else 0.1
    chart_likelihood += 0.25 if 0.4 < aspect < 4.0 else 0.0
    chart_likelihood += 0.25 if edge_density > 6 else 0.0
    chart_likelihood += 0.25 if len(chromatic) >= 2 else 0.0
    chart_likelihood = min(1.0, chart_likelihood)

    if chart_likelihood >= 0.7:
        image_kind = "chart or figure"
    elif chart_likelihood >= 0.4:
        image_kind = "screenshot or annotated figure"
    else:
        image_kind = "photograph or non-chart image"

    summary = (
        f"This looks like a {image_kind}. " + " ".join(observations[:3])
    )
    technical = (
        f"Image {width}×{height}px; background {_rgb_to_hex(tuple(background))} "
        f"({'dark' if dark_background else 'light'}); mean luminance {brightness:.1f}; "
        f"contrast {contrast:.1f}; edge density {edge_density:.1f}; saturation {colourfulness:.1f}%; "
        f"background share {whitespace:.1f}%. Plot-region proxy {plot_region}. "
        f"Chart-likelihood heuristic {chart_likelihood:.2f}."
    )

    return {
        "kind": image_kind,
        "summary": summary,
        "simple": (
            summary
            + " I can describe the visual structure, but I cannot reliably read exact axis numbers "
            "from an image. For numerical interpretation, generate the chart inside AQACT so I can use the underlying data."
        ),
        "technical": technical,
        "observations": observations,
        "palette": palette,
        "metrics": {
            "width": width,
            "height": height,
            "aspect_ratio": round(aspect, 3),
            "background_hex": _rgb_to_hex(tuple(background)),
            "dark_background": dark_background,
            "brightness": round(brightness, 2),
            "contrast": round(contrast, 2),
            "edge_density": round(edge_density, 3),
            "colourfulness_pct": round(colourfulness, 2),
            "empty_space_pct": round(whitespace, 2),
            "chart_likelihood": round(chart_likelihood, 2),
            "plot_region": plot_region,
        },
        "caveats": [
            "Exact values, labels, and units cannot be guaranteed from pixels alone.",
            "Colour mapping and compression can change the apparent palette.",
            "For scientific reporting, use the underlying data rather than a screenshot.",
        ],
        "suggested_questions": [
            "Describe the visual structure of this figure.",
            "Does this look like a time series or a distribution chart?",
            "What should I check before publishing this figure?",
        ],
    }
