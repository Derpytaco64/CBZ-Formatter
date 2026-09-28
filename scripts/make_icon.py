"""Builds the CBZ Forge app icon by compositing a source character-art
image onto a closed-manga-book shape (spine + stacked page edges + cover
art inset), then exports a .png (for the window icon) and a multi-resolution
.ico (for the PyInstaller-built executable's own icon).

Usage:
    python scripts/make_icon.py <source_image> [output_dir]

output_dir defaults to cbzforge/gui/resources, which is already picked up
by cbzforge.spec's collect_data_files('cbzforge') call, so nothing else
needs to change to bundle it into the portable build.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

CANVAS = 1024
BOOK_W, BOOK_H = 760, 900
RADIUS = 36
SPINE_FRACTION = 0.10


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size[0] - 1, size[1] - 1)], radius=radius, fill=255)
    return mask


def build_icon(source_path: Path) -> Image.Image:
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    book_x = (CANVAS - BOOK_W) // 2 - 20
    book_y = (CANVAS - BOOK_H) // 2

    # Soft drop shadow behind the whole book.
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [book_x + 18, book_y + 26, book_x + BOOK_W + 18, book_y + BOOK_H + 26],
        radius=RADIUS,
        fill=(0, 0, 0, 130),
    )
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24)))

    # Stacked page edges peeking out behind the front cover, like a closed
    # book viewed at a slight angle.
    for i in range(5, 0, -1):
        offset = i * 3
        shade = max(210, 255 - i * 6)
        page = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        ImageDraw.Draw(page).rounded_rectangle(
            [book_x + offset, book_y + offset, book_x + BOOK_W + offset, book_y + BOOK_H + offset],
            radius=RADIUS,
            fill=(shade, shade - 6, shade - 20, 255),
        )
        canvas.alpha_composite(page)

    # Front cover: dark base + darker spine strip on the left.
    cover = Image.new("RGBA", (BOOK_W, BOOK_H), (0, 0, 0, 0))
    cover_draw = ImageDraw.Draw(cover)
    cover_draw.rounded_rectangle([(0, 0), (BOOK_W - 1, BOOK_H - 1)], radius=RADIUS, fill=(30, 30, 34, 255))
    spine_w = int(BOOK_W * SPINE_FRACTION)
    cover_draw.rectangle([(0, 0), (spine_w, BOOK_H)], fill=(18, 18, 20, 255))
    cover.putalpha(_rounded_mask((BOOK_W, BOOK_H), RADIUS))

    # Cover art inset, cropped to fill its slot without distortion.
    art = ImageOps.exif_transpose(Image.open(source_path)).convert("RGBA")
    art_w, art_h = BOOK_W - spine_w - 60, BOOK_H - 90
    art = ImageOps.fit(art, (art_w, art_h), Image.LANCZOS)
    art_x, art_y = spine_w + 30, 45
    cover.paste(art, (art_x, art_y), _rounded_mask((art_w, art_h), 18))
    cover_draw.rounded_rectangle(
        [art_x, art_y, art_x + art_w, art_y + art_h], radius=18, outline=(255, 255, 255, 220), width=4
    )

    canvas.alpha_composite(cover, (book_x, book_y))
    return canvas


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python scripts/make_icon.py <source_image> [output_dir]")
        raise SystemExit(1)

    source_path = Path(sys.argv[1])
    out_dir = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else Path(__file__).parent.parent / "cbzforge" / "gui" / "resources"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    icon = build_icon(source_path)
    png_path = out_dir / "app_icon.png"
    ico_path = out_dir / "app_icon.ico"
    icon.save(png_path)
    icon.save(ico_path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print(f"Wrote {png_path} and {ico_path}")


if __name__ == "__main__":
    main()
