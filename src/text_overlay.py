#!/usr/bin/env python3
"""Pillow text overlay — classic Impact-style meme captions.

White fill + black stroke, all-caps, auto-fit width, top/bottom anchored.
Designed to be visually indistinguishable from r/memes-tier output even
when using a free Impact substitute (Anton).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "fonts"

# Preferred fonts in order. Anton is a free, OFL-licensed Impact lookalike.
FONT_CANDIDATES: tuple[str, ...] = (
    "impact.ttf",
    "Impact.ttf",
    "Anton-Regular.ttf",
    "anton.ttf",
)

# Fallback to a system font if nothing in fonts/ is available.
SYSTEM_FALLBACKS: tuple[str, ...] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


# Per-template text regions in NORMALISED coords (x1, y1, x2, y2) where each
# value is 0..1 of the template's width/height. Order = caption order.
# Keyed by Imgflip template id (string).
#
# Captured by eyeballing the actual template images; tweak as needed.
TEMPLATE_REGIONS: dict[str, list[tuple[float, float, float, float]]] = {
    # Drake Hotline Bling — 2 panels stacked. Classic style: text overlaid
    # across the full panel (over the photo + side strip), centred.
    "181913649": [(0.00, 0.00, 1.00, 0.50),
                  (0.00, 0.50, 1.00, 1.00)],
    # Distracted Boyfriend — 3 labels: girlfriend (left), boyfriend (mid),
    # other woman (right). Place near the bottom of each subject.
    "112126428": [(0.62, 0.30, 1.00, 0.55),   # other woman (right)
                  (0.30, 0.55, 0.62, 0.85),   # boyfriend (mid)
                  (0.00, 0.55, 0.30, 0.85)],  # girlfriend (left)
    # Two Buttons — sweating guy with two red buttons at the top.
    "87743020":  [(0.00, 0.00, 0.50, 0.30),   # left button
                  (0.50, 0.00, 1.00, 0.30)],  # right button
    # Expanding Brain — 4 panels stacked. Auto-trim removes the right white
    # sidebar; regions span the full panel post-trim.
    "93895088":  [(0.00, 0.00, 1.00, 0.25),
                  (0.00, 0.25, 1.00, 0.50),
                  (0.00, 0.50, 1.00, 0.75),
                  (0.00, 0.75, 1.00, 1.00)],
    # Tuxedo Winnie The Pooh — 2 panels stacked, text overlaid on each panel.
    "178591752": [(0.00, 0.00, 1.00, 0.50),
                  (0.00, 0.50, 1.00, 1.00)],
    # Buff Doge vs. Cheems — 2 panels side by side, text at the bottom of each.
    "247375501": [(0.00, 0.70, 0.50, 1.00),
                  (0.50, 0.70, 1.00, 1.00)],
    # Woman Yelling At Cat — 2 panels side by side; text along top of each.
    "188390779": [(0.00, 0.00, 0.50, 0.20),
                  (0.50, 0.00, 1.00, 0.20)],
    # Change My Mind — single text region on the cardboard sign.
    "129242436": [(0.30, 0.55, 0.95, 0.85)],
    # Mocking Spongebob — single line of mocking text along the top.
    "102156234": [(0.00, 0.00, 1.00, 0.20)],
    # Left Exit 12 Off Ramp — 3 labels: exit sign, going-straight, swerving.
    "124822590": [(0.05, 0.10, 0.45, 0.35),   # exit sign label
                  (0.55, 0.10, 0.95, 0.35),   # straight-on label
                  (0.30, 0.65, 0.95, 0.95)],  # car/swerve label
}


def resolve_font(font_dir: Path = FONTS_DIR) -> str:
    """Return path to the best available meme font."""
    for name in FONT_CANDIDATES:
        candidate = font_dir / name
        if candidate.exists():
            return str(candidate)
    for sysfont in SYSTEM_FALLBACKS:
        if Path(sysfont).exists():
            return sysfont
    raise FileNotFoundError(
        "No usable font found. Drop Anton-Regular.ttf into "
        f"{font_dir} or install fonts-dejavu-core."
    )


def _wrap_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """Greedy word-wrap so each line fits inside max_width pixels."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    max_width: int,
    max_height: int,
    start_size: int,
    min_size: int = 14,
    max_lines: int = 2,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Shrink the font until wrapped text fits inside max_width x max_height
    *and* uses no more than max_lines lines."""
    size = start_size
    while size >= min_size:
        font = ImageFont.truetype(font_path, size=size)
        lines = _wrap_to_width(draw, text, font, max_width)
        if len(lines) > max_lines:
            size -= 2
            continue
        line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        total_h = int(line_h * len(lines) * 1.15)
        widest = max(
            (draw.textbbox((0, 0), ln, font=font)[2] for ln in lines),
            default=0,
        )
        if total_h <= max_height and widest <= max_width:
            return font, lines
        size -= 2
    # Give up gracefully: smallest font + best-effort wrap, truncated to max_lines.
    font = ImageFont.truetype(font_path, size=min_size)
    lines = _wrap_to_width(draw, text, font, max_width)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return font, lines


def _draw_block(
    draw: ImageDraw.ImageDraw,
    lines: Iterable[str],
    font: ImageFont.FreeTypeFont,
    image_size: tuple[int, int],
    anchor: str,  # "top" or "bottom"
    padding: int,
    stroke_width: int,
) -> None:
    img_w, img_h = image_size
    line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
    spacing = int(line_h * 0.15)
    block_h = (line_h + spacing) * len(list(lines))
    # Re-collect lines after the generator consumption above.
    # (Caller passes a list anyway; this is defensive.)


def draw_meme_text(
    image: Image.Image,
    top: str = "",
    bottom: str = "",
    font_path: str | None = None,
    padding_ratio: float = 0.04,
    stroke_ratio: float = 0.04,
    max_text_height_ratio: float = 0.28,
) -> Image.Image:
    """Return a new image with classic top/bottom meme text overlaid."""
    if image.mode != "RGB":
        image = image.convert("RGB")
    img = image.copy()
    draw = ImageDraw.Draw(img)
    img_w, img_h = img.size
    padding = max(8, int(min(img_w, img_h) * padding_ratio))
    max_width = img_w - 2 * padding
    max_height = int(img_h * max_text_height_ratio)
    start_size = max(28, int(img_h * 0.11))
    font_path = font_path or resolve_font()

    def _render_block(text: str, anchor: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        text = text.upper()
        font, lines = _fit_font(
            draw, text, font_path, max_width, max_height, start_size,
        )
        line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
        spacing = int(line_h * 0.15)
        block_h = (line_h + spacing) * len(lines) - spacing
        if anchor == "top":
            y = padding
        else:
            y = img_h - padding - block_h
        stroke_w = max(2, int(font.size * stroke_ratio))
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            line_w = bbox[2] - bbox[0]
            x = (img_w - line_w) // 2
            draw.text(
                (x, y),
                line,
                font=font,
                fill="white",
                stroke_width=stroke_w,
                stroke_fill="black",
            )
            y += line_h + spacing

    _render_block(top, "top")
    _render_block(bottom, "bottom")
    return img


def _draw_in_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    font_path: str,
    stroke_ratio: float = 0.06,
    max_lines: int = 2,
    inset_ratio: float = 0.06,
) -> None:
    """Render a single caption inside an (x1, y1, x2, y2) box, centred.

    Auto-shrinks font and wraps to at most `max_lines` lines.
    """
    text = (text or "").strip()
    if not text:
        return
    text = text.upper()
    x1, y1, x2, y2 = box
    raw_w = max(1, x2 - x1)
    raw_h = max(1, y2 - y1)
    inset_x = int(raw_w * inset_ratio)
    inset_y = int(raw_h * inset_ratio)
    box_w = max(1, raw_w - 2 * inset_x)
    box_h = max(1, raw_h - 2 * inset_y)
    # Sensible starting size: ~30% of box height, tunable upwards by max_lines.
    start_size = max(24, int(box_h * 0.32))
    font, lines = _fit_font(
        draw, text, font_path,
        max_width=box_w,
        max_height=box_h,
        start_size=start_size,
        min_size=14,
        max_lines=max_lines,
    )
    line_h = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
    spacing = int(line_h * 0.15)
    block_h = (line_h + spacing) * len(lines) - spacing
    # Centre vertically inside the inset region.
    cx = x1 + inset_x
    cy = y1 + inset_y
    y = cy + max(0, (box_h - block_h) // 2)
    stroke_w = max(3, int(font.size * stroke_ratio))
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = cx + (box_w - line_w) // 2
        draw.text(
            (x, y),
            line,
            font=font,
            fill="white",
            stroke_width=stroke_w,
            stroke_fill="black",
        )
        y += line_h + spacing


def draw_meme_panels(
    image: Image.Image,
    captions: list[str],
    regions: list[tuple[float, float, float, float]],
    font_path: str | None = None,
    stroke_ratio: float = 0.04,
) -> Image.Image:
    """Render captions inside per-template regions (normalised 0..1 coords).

    `captions` and `regions` are matched by index. Extra captions are ignored;
    extra regions stay empty.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    img = image.copy()
    draw = ImageDraw.Draw(img)
    img_w, img_h = img.size
    font_path = font_path or resolve_font()
    for caption, region in zip(captions, regions):
        x1 = int(region[0] * img_w)
        y1 = int(region[1] * img_h)
        x2 = int(region[2] * img_w)
        y2 = int(region[3] * img_h)
        _draw_in_box(draw, caption, (x1, y1, x2, y2), font_path,
                     stroke_ratio=stroke_ratio)
    return img
