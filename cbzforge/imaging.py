"""Pixel-level operations on page image bytes - kept separate from Qt (widgets.py)
so it's usable from pack.py/tests without a GUI dependency.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image


def apply_crop(image_bytes: bytes, crop_box: list[int]) -> bytes:
    """Crop to (left, top, right, bottom) in the source image's own pixel
    coordinates, preserving the original format."""
    img = Image.open(BytesIO(image_bytes))
    fmt = img.format or "PNG"
    cropped = img.crop(tuple(crop_box))
    buf = BytesIO()
    cropped.save(buf, format=fmt)
    return buf.getvalue()


def get_image_size(image_bytes: bytes) -> tuple[int, int]:
    return Image.open(BytesIO(image_bytes)).size
