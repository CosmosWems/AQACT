"""Publication-quality figure export.

Plotly renders interactive charts in the browser.  For figures that end up in a
report or paper we need something more deliberate: a white page, consistent
margins, a self-contained title, an optional caption, provenance metadata and a
raster resolution chosen for print.

The browser sends the rendered chart (PNG data URL or Plotly SVG markup) to this
blueprint.  Pillow composes the page locally for PNG/PDF; SVG is composed as a
true vector document.  Nothing is uploaded to a third-party service.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, jsonify, request, send_file
from PIL import Image, ImageDraw, ImageFont
from werkzeug.utils import secure_filename

from core.errors import ApiError

bp = Blueprint("export_api", __name__, url_prefix="/api/export")

# Page sizes in inches (width, height).
PAGE_SIZES = {
    "a4": (8.27, 11.69),
    "a4-landscape": (11.69, 8.27),
    "a3": (11.69, 16.54),
    "a3-landscape": (16.54, 11.69),
    "letter": (8.5, 11.0),
    "letter-landscape": (11.0, 8.5),
    "square": (8.0, 8.0),
    "wide": (12.0, 7.0),
    "slide": (13.333, 7.5),
}

MAX_IMAGE_BYTES = 40 * 1024 * 1024
DEFAULT_DPI = 300
INK = (17, 24, 39)
MUTED = (100, 116, 139)
ACCENT = (37, 99, 235)
RULE = (226, 232, 240)

_FONT_CANDIDATES = {
    False: [
        ("C:/Windows/Fonts/segoeui.ttf", None),
        ("C:/Windows/Fonts/arial.ttf", None),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", None),
    ],
    True: [
        ("C:/Windows/Fonts/segoeuib.ttf", None),
        ("C:/Windows/Fonts/arialbd.ttf", None),
        ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", None),
    ],
}


def _load_font(px: int, bold: bool = False):
    for path, _ in _FONT_CANDIDATES[bold]:
        try:
            return ImageFont.truetype(path, size=max(6, int(px)))
        except OSError:
            continue
    return ImageFont.load_default()


def _decode_image(value: Optional[str]) -> Optional[bytes]:
    if not value or not isinstance(value, str):
        return None
    payload = value.split(",", 1)[-1] if value.startswith("data:") else value
    try:
        raw = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ApiError("The chart image could not be decoded.", 400,
                       code="bad_figure_image",
                       hint="Re-render the chart and export again.") from exc
    if not raw:
        return None
    if len(raw) > MAX_IMAGE_BYTES:
        raise ApiError("The chart image is too large to compose.", 413,
                       code="figure_image_too_large",
                       hint="Export at a lower scale (1x or 2x) and try again.")
    return raw


def _read_request_image() -> Optional[bytes]:
    upload = request.files.get("image") if request.files else None
    if upload and upload.filename:
        raw = upload.read()
        if raw:
            return raw
    return None


def _open_image(raw: Optional[bytes]):
    if not raw:
        return None
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:  # noqa: BLE001
        raise ApiError("The chart image could not be opened.", 400,
                       code="figure_image_invalid",
                       hint="Use a PNG or JPEG exported from the chart.") from exc
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    return image


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_lines(draw, lines, x, y, font, fill, line_height):
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
    if width <= 0 or height <= 0:
        return image
    ratio = min(width / image.width, height / image.height)
    size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
    return image.resize(size, Image.LANCZOS)


def _compose_page(image: Optional[Image.Image], *, width_in: float, height_in: float,
                  dpi: int, title: str, subtitle: str, caption: str,
                  source: str, figure_number: str, footer: str) -> Image.Image:
    width = max(640, int(round(width_in * dpi)))
    height = max(480, int(round(height_in * dpi)))
    page = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(page)

    unit = dpi / 72.0
    margin = int(round(0.55 * dpi))
    title_px = int(round(19 * unit))
    subtitle_px = int(round(10.5 * unit))
    body_px = int(round(9 * unit))
    small_px = int(round(7.5 * unit))

    title_font = _load_font(title_px, bold=True)
    subtitle_font = _load_font(subtitle_px)
    body_font = _load_font(body_px)
    small_font = _load_font(small_px)

    # Thin accent rule at the very top of the page.
    draw.rectangle([0, 0, width, max(2, int(0.06 * dpi))], fill=ACCENT)

    left = margin
    right = width - margin
    content_width = right - left
    y = margin

    brand = "AQACT  ·  AIR QUALITY ANALYSIS"
    draw.text((left, y), brand, font=small_font, fill=MUTED)
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    stamp_w = draw.textlength(stamp, font=small_font)
    draw.text((right - stamp_w, y), stamp, font=small_font, fill=MUTED)
    y += int(round(0.30 * dpi))

    heading = f"Figure {figure_number}. {title}" if figure_number else title
    title_lines = _wrap(draw, heading, title_font, content_width)
    y = _draw_lines(draw, title_lines, left, y, title_font, INK, int(title_px * 1.25))
    if subtitle:
        sub_lines = _wrap(draw, subtitle, subtitle_font, content_width)
        y += int(round(0.06 * dpi))
        y = _draw_lines(draw, sub_lines, left, y, subtitle_font, MUTED, int(subtitle_px * 1.35))
    y += int(round(0.18 * dpi))
    draw.line([left, y, right, y], fill=RULE, width=max(1, dpi // 300))
    y += int(round(0.20 * dpi))

    # Reserve caption + footer space before sizing the figure.
    footer_h = int(round(0.45 * dpi))
    caption_lines = _wrap(draw, caption, body_font, content_width) if caption else []
    caption_h = int(len(caption_lines) * body_px * 1.45 + 0.14 * dpi) if caption_lines else 0
    bottom_reserved = margin + footer_h + caption_h
    plot_top = y
    plot_bottom = height - bottom_reserved
    plot_w = content_width
    plot_h = max(120, plot_bottom - plot_top)

    draw.rectangle([left - 1, plot_top - 1, right + 1, plot_top + plot_h + 1],
                   outline=RULE, width=max(1, dpi // 300))
    if image is not None:
        fitted = _fit(image, plot_w - int(0.10 * dpi), plot_h - int(0.10 * dpi))
        page.paste(fitted, (left + (plot_w - fitted.width) // 2,
                            plot_top + (plot_h - fitted.height) // 2))
    else:
        notice = "Chart rendering is unavailable on the server. Export the chart from the browser."
        tw = draw.textlength(notice, font=body_font)
        draw.text((left + (plot_w - tw) / 2, plot_top + plot_h / 2 - body_px),
                  notice, font=body_font, fill=MUTED)

    y = plot_top + plot_h + int(round(0.16 * dpi))
    if caption_lines:
        y = _draw_lines(draw, caption_lines, left, y, body_font, INK, int(body_px * 1.45))
    y += int(round(0.10 * dpi))
    draw.line([left, height - margin - footer_h + int(0.08 * dpi),
               right, height - margin - footer_h + int(0.08 * dpi)],
              fill=RULE, width=max(1, dpi // 300))
    footer_text = footer or source or "AQACT — offline air quality analytics"
    draw.text((left, height - margin - int(0.20 * dpi)), footer_text,
              font=small_font, fill=MUTED)
    if source and source not in footer_text:
        src_w = draw.textlength(source, font=small_font)
        draw.text((right - src_w, height - margin - int(0.20 * dpi)), source,
                  font=small_font, fill=MUTED)
    return page


def _page_dimensions(body: dict) -> tuple[float, float]:
    size = str(body.get("size") or "auto").lower()
    if size in PAGE_SIZES:
        return PAGE_SIZES[size]
    width = float(body.get("width") or 12.0)
    height = float(body.get("height") or 7.0)
    return (max(3.0, min(width, 30.0)), max(3.0, min(height, 30.0)))


def _clean_text(value, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _compose_svg(*, svg: str, image: Optional[Image.Image], image_raw: Optional[bytes],
                 width_in: float, height_in: float, dpi: int, title: str,
                 subtitle: str, caption: str, source: str, figure_number: str,
                 footer: str) -> str:
    """Return a self-contained SVG with the chart placed inside a figure frame."""
    width = max(640, int(round(width_in * dpi)))
    height = max(480, int(round(height_in * dpi)))
    margin = int(round(0.55 * dpi))
    title_px = int(round(19 * dpi / 72))
    subtitle_px = int(round(10.5 * dpi / 72))
    body_px = int(round(9 * dpi / 72))
    small_px = int(round(7.5 * dpi / 72))

    heading = f"Figure {figure_number}. {title}" if figure_number else title
    # Rough text metrics without a font engine: ~0.55em average advance.
    chars_per_line = max(12, int((width - 2 * margin) / (title_px * 0.55)))
    title_lines = _chunk(heading, chars_per_line)
    sub_lines = _chunk(subtitle, max(12, int((width - 2 * margin) / (subtitle_px * 0.52))))
    cap_lines = _chunk(caption, max(12, int((width - 2 * margin) / (body_px * 0.52))))

    top = margin + int(0.30 * dpi)
    title_h = len(title_lines) * int(title_px * 1.25)
    sub_h = len(sub_lines) * int(subtitle_px * 1.35) + (int(0.06 * dpi) if sub_lines else 0)
    cap_h = len(cap_lines) * int(body_px * 1.45) + (int(0.26 * dpi) if cap_lines else 0)
    footer_h = int(0.65 * dpi)
    plot_top = top + title_h + sub_h + int(0.38 * dpi)
    plot_left = margin
    plot_w = width - 2 * margin
    plot_h = max(120, height - plot_top - cap_h - footer_h - margin)
    caption_y = plot_top + plot_h + int(0.20 * dpi)

    def esc(text: str) -> str:
        return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))

    def text_lines(lines, x, y, size, weight, fill, line_height):
        out = []
        for i, line in enumerate(lines):
            out.append(
                f'<text x="{x}" y="{y + i * line_height}" font-family="Segoe UI, Arial, sans-serif" '
                f'font-size="{size}" font-weight="{weight}" fill="{fill}">{esc(line)}</text>'
            )
        return "\n".join(out)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<metadata>" + esc(json.dumps({
            "generator": "AQACT",
            "title": title,
            "subtitle": subtitle,
            "caption": caption,
            "source": source,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        })) + "</metadata>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<rect width="{width}" height="{max(2, int(0.06 * dpi))}" fill="#2563eb"/>',
    ]
    parts.append(text_lines(["AQACT  ·  AIR QUALITY ANALYSIS"], margin, margin + small_px,
                            small_px, 600, "#64748b", small_px))
    stamp = datetime.now(timezone.utc).strftime("%d %b %Y")
    parts.append(
        f'<text x="{width - margin}" y="{margin + small_px}" text-anchor="end" '
        f'font-family="Segoe UI, Arial, sans-serif" font-size="{small_px}" fill="#64748b">{stamp}</text>'
    )
    parts.append(text_lines(title_lines, margin, top + title_px, title_px, 700, "#111827",
                            int(title_px * 1.25)))
    if sub_lines:
        parts.append(text_lines(sub_lines, margin, top + title_h + int(0.06 * dpi) + subtitle_px,
                                subtitle_px, 400, "#64748b", int(subtitle_px * 1.35)))
    rule_y = plot_top - int(0.20 * dpi)
    parts.append(f'<line x1="{margin}" y1="{rule_y}" x2="{width - margin}" y2="{rule_y}" '
                 f'stroke="#e2e8f0" stroke-width="1"/>')
    parts.append(f'<rect x="{plot_left}" y="{plot_top}" width="{plot_w}" height="{plot_h}" '
                 f'fill="#ffffff" stroke="#e2e8f0" stroke-width="1"/>')

    if svg:
        inner = svg.strip()
        inner = re.sub(r"^<\?xml[^>]*\?>", "", inner).strip()
        wrapper = re.search(r"<svg\b([^>]*)>(.*)</svg>\s*$", inner, re.S)
        if wrapper:
            attrs, body = wrapper.group(1), wrapper.group(2)
            vb = re.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', attrs)
            w_attr = re.search(r'\bwidth\s*=\s*["\']([0-9.]+)', attrs)
            h_attr = re.search(r'\bheight\s*=\s*["\']([0-9.]+)', attrs)
            src_w = src_h = None
            if vb:
                parts_vb = [float(v) for v in re.split(r"[\s,]+", vb.group(1).strip()) if v]
                if len(parts_vb) == 4:
                    src_w, src_h = parts_vb[2], parts_vb[3]
            if (not src_w or not src_h) and w_attr and h_attr:
                src_w, src_h = float(w_attr.group(1)), float(h_attr.group(1))
            src_w = src_w or plot_w
            src_h = src_h or plot_h
            scale = min(plot_w / src_w, plot_h / src_h) * 0.94
            tx = plot_left + (plot_w - src_w * scale) / 2
            ty = plot_top + (plot_h - src_h * scale) / 2
            parts.append(f'<g transform="translate({tx:.2f},{ty:.2f}) scale({scale:.5f})">{body}</g>')
    elif image_raw:
        encoded = base64.b64encode(image_raw).decode("ascii")
        mime = "image/png"
        try:
            fmt = Image.open(io.BytesIO(image_raw)).format or "PNG"
            mime = "image/jpeg" if fmt.upper() in ("JPEG", "JPG") else "image/png"
        except Exception:  # noqa: BLE001
            pass
        fitted = _fit(image, plot_w, plot_h) if image is not None else None
        draw_w = fitted.width if fitted else plot_w
        draw_h = fitted.height if fitted else plot_h
        x = plot_left + (plot_w - draw_w) / 2
        y = plot_top + (plot_h - draw_h) / 2
        parts.append(f'<image x="{x:.2f}" y="{y:.2f}" width="{draw_w}" height="{draw_h}" '
                     f'preserveAspectRatio="xMidYMid meet" xlink:href="data:{mime};base64,{encoded}"/>')

    if cap_lines:
        parts.append(text_lines(cap_lines, margin, caption_y + body_px, body_px, 400,
                                "#111827", int(body_px * 1.45)))
    footer_y = height - margin - int(0.18 * dpi)
    parts.append(f'<line x1="{margin}" y1="{footer_y - int(0.16 * dpi)}" '
                 f'x2="{width - margin}" y2="{footer_y - int(0.16 * dpi)}" stroke="#e2e8f0"/>')
    footer_text = footer or source or "AQACT — offline air quality analytics"
    parts.append(text_lines([footer_text], margin, footer_y, small_px, 400, "#64748b", small_px))
    if source and source not in footer_text:
        parts.append(
            f'<text x="{width - margin}" y="{footer_y}" text-anchor="end" '
            f'font-family="Segoe UI, Arial, sans-serif" font-size="{small_px}" fill="#64748b">{esc(source)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _chunk(text: str, max_chars: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    lines, current = [], words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


@bp.get("/capabilities")
def capabilities():
    return jsonify({
        "ok": True,
        "formats": ["png", "pdf", "svg"],
        "page_sizes": sorted(PAGE_SIZES),
        "default_size": "wide",
        "default_dpi": DEFAULT_DPI,
        "max_dpi": 600,
        "server_side_plotly": False,
        "notes": [
            "Render the chart in the browser and send its PNG/SVG to this endpoint.",
            "PDF and PNG pages are composed locally with Pillow; no network access is used.",
        ],
    })


@bp.post("/figure")
def export_figure():
    body = request.get_json(silent=True) or {}
    fmt = str(body.get("format") or request.args.get("format") or "pdf").lower()
    if fmt not in {"png", "pdf", "svg"}:
        raise ApiError(f"Unsupported export format '{fmt}'.", 400,
                       code="unsupported_export_format",
                       hint="Choose PNG, PDF or SVG.")

    image_raw = _read_request_image() or _decode_image(body.get("image") or body.get("image_data_url"))
    image = _open_image(image_raw)
    svg = body.get("svg") if isinstance(body.get("svg"), str) else None

    figure = body.get("figure") if isinstance(body.get("figure"), dict) else {}
    layout = figure.get("layout") if isinstance(figure, dict) else {}
    layout_title = layout.get("title") if isinstance(layout, dict) else None
    if isinstance(layout_title, dict):
        layout_title = layout_title.get("text")

    title = _clean_text(body.get("title") or layout_title or "Air quality analysis", 240)
    subtitle = _clean_text(body.get("subtitle"), 400)
    caption = _clean_text(body.get("caption"), 1200)
    source = _clean_text(body.get("source"), 240)
    footer = _clean_text(body.get("footer"), 240)
    figure_number = _clean_text(body.get("figure_number"), 24)
    width_in, height_in = _page_dimensions(body)
    try:
        dpi = int(body.get("dpi") or DEFAULT_DPI)
    except (TypeError, ValueError):
        dpi = DEFAULT_DPI
    dpi = max(96, min(600, dpi))

    if fmt in {"png", "pdf"} and image is None:
        raise ApiError("A rendered chart image is required for PNG/PDF export.", 400,
                       code="missing_chart_image",
                       hint="Use the Download button in the chart toolbar; the app sends the rendered image automatically.")

    filename_base = secure_filename(body.get("filename") or title) or "aqact_figure"
    filename_base = filename_base[:80]

    if fmt == "svg":
        document = _compose_svg(
            svg=svg or "", image=image, image_raw=image_raw,
            width_in=width_in, height_in=height_in, dpi=dpi, title=title,
            subtitle=subtitle, caption=caption, source=source,
            figure_number=figure_number, footer=footer,
        )
        buf = io.BytesIO(document.encode("utf-8"))
        return send_file(buf, mimetype="image/svg+xml", as_attachment=True,
                         download_name=f"{filename_base}.svg")

    page = _compose_page(
        image, width_in=width_in, height_in=height_in, dpi=dpi, title=title,
        subtitle=subtitle, caption=caption, source=source,
        figure_number=figure_number, footer=footer,
    )
    buf = io.BytesIO()
    if fmt == "pdf":
        page.save(buf, format="PDF", resolution=dpi)
        mimetype = "application/pdf"
        ext = "pdf"
    else:
        page.save(buf, format="PNG", optimize=True)
        mimetype = "image/png"
        ext = "png"
    buf.seek(0)
    return send_file(buf, mimetype=mimetype, as_attachment=True,
                     download_name=f"{filename_base}.{ext}")
