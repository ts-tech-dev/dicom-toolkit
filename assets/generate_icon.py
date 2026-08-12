#!/usr/bin/env python3
"""
assets/generate_icon.py
==========================
One-off generator for the app icon (assets/icon.png, assets/icon.ico).
Drawn programmatically with Pillow rather than checked in from an
external design tool, so there's no binary-asset provenance question and
it's trivial to tweak (colors, shape) and regenerate.

Run with: python assets/generate_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SIZE = 512
OUT_DIR = Path(__file__).parent

# A DICOM/PACS-flavored mark: a rounded square in "medical blue", holding a
# white image-frame with corner viewfinder brackets (this is an imaging
# tool) and a heartbeat/EKG pulse line through the middle (this is medical).
# Kept to a few bold shapes, no fine detail, so it still reads at 16x16.
TOP_COLOR = (28, 92, 150)      # lighter medical blue
BOTTOM_COLOR = (12, 46, 82)    # darker medical blue
FRAME_COLOR = (255, 255, 255)
PULSE_COLOR = (74, 222, 196)   # teal accent


def _gradient_square(size: int, top, bottom) -> Image.Image:
    t = np.linspace(0, 1, size).reshape(size, 1, 1)
    row = np.array(top) * (1 - t) + np.array(bottom) * t
    arr = np.repeat(row, size, axis=1).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def build_icon() -> Image.Image:
    bg = _gradient_square(SIZE, TOP_COLOR, BOTTOM_COLOR)
    mask = _rounded_mask(SIZE, radius=SIZE // 5)
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(bg, (0, 0), mask)

    draw = ImageDraw.Draw(icon)

    # -- corner viewfinder brackets (suggests "imaging/scan") ------------
    margin = SIZE * 0.16
    bracket_len = SIZE * 0.14
    stroke = max(6, SIZE // 48)
    corners = [
        (margin, margin, 1, 1),                          # top-left
        (SIZE - margin, margin, -1, 1),                   # top-right
        (margin, SIZE - margin, 1, -1),                    # bottom-left
        (SIZE - margin, SIZE - margin, -1, -1),              # bottom-right
    ]
    for x, y, dx, dy in corners:
        draw.line([(x, y), (x + dx * bracket_len, y)], fill=FRAME_COLOR, width=stroke)
        draw.line([(x, y), (x, y + dy * bracket_len)], fill=FRAME_COLOR, width=stroke)

    # -- heartbeat / EKG pulse line through the center --------------------
    cy = SIZE * 0.52
    amp = SIZE * 0.16
    pts = [
        (SIZE * 0.18, cy),
        (SIZE * 0.34, cy),
        (SIZE * 0.40, cy - amp * 0.6),
        (SIZE * 0.46, cy + amp * 1.3),
        (SIZE * 0.52, cy - amp * 1.6),
        (SIZE * 0.58, cy + amp * 0.4),
        (SIZE * 0.64, cy),
        (SIZE * 0.82, cy),
    ]
    pulse_stroke = max(8, SIZE // 36)
    draw.line(pts, fill=PULSE_COLOR, width=pulse_stroke, joint="curve")
    r = pulse_stroke / 2
    for x, y in (pts[0], pts[-1]):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=PULSE_COLOR)

    return icon


def main() -> None:
    icon = build_icon()
    png_path = OUT_DIR / "icon.png"
    icon.save(png_path)

    ico_path = OUT_DIR / "icon.ico"
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    icon.save(ico_path, sizes=sizes)

    print(f"Wrote {png_path} and {ico_path}")


if __name__ == "__main__":
    main()
