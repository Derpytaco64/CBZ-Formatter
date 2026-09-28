"""Page image loading for the GUI - shared by the thumbnail grid and the
larger preview/OCR panel so there's one place that knows how to pull page
bytes out of either a zip source or a directory source.

Two different "views" of a page matter here:
- The *effective* view (load_page_image_bytes / load_thumbnail_icon /
  load_preview_pixmap): reflects the page's current edits (crop applied if
  set, greyed out if excluded) - "what you'll actually get," matching
  exactly what pack.build_cbz will write.
- The *original* view (load_original_pixmap_and_size): always the
  untouched source bytes, ignoring any existing crop - used only while the
  crop/split tool is actively selecting a new region, since composing a
  new crop on top of an already-cropped preview would require reasoning
  about two overlapping coordinate spaces for no real benefit (crop_box is
  a single non-destructive edit, not a stack - drawing a new selection
  always starts fresh from the original and replaces it).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from cbzforge.imaging import apply_crop, get_image_size
from cbzforge.manifest import Manifest


def load_page_bytes(manifest: Manifest, page_index: int) -> bytes:
    """Always the original, untouched source bytes for this page."""
    page = manifest.pages[page_index]
    source = Path(manifest.source)
    is_zip = source.is_file() and source.suffix.lower() in {".cbz", ".zip"}
    if is_zip:
        with zipfile.ZipFile(source) as z:
            return z.read(page.source_name)
    return (source / page.source_name).read_bytes()


def load_page_image_bytes(manifest: Manifest, page_index: int) -> bytes:
    """Bytes reflecting the current edit state (crop applied if set) -
    exactly what pack.build_cbz will write for this page."""
    page = manifest.pages[page_index]
    data = load_page_bytes(manifest, page_index)
    if page.crop_box:
        data = apply_crop(data, page.crop_box)
    return data


def _excluded_overlay(pixmap: QPixmap) -> QPixmap:
    if pixmap.isNull():
        return pixmap
    result = QPixmap(pixmap.size())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, pixmap)
    painter.fillRect(result.rect(), QColor(90, 90, 90, 170))
    painter.setPen(QColor(255, 255, 255))
    font = painter.font()
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(result.rect(), Qt.AlignCenter, "EXCLUDED")
    painter.end()
    return result


def load_thumbnail_icon(manifest: Manifest, page_index: int, height: int = 150) -> QIcon:
    data = load_page_image_bytes(manifest, page_index)
    pixmap = QPixmap()
    pixmap.loadFromData(data)
    if not pixmap.isNull():
        pixmap = pixmap.scaledToHeight(height, Qt.SmoothTransformation)
    if manifest.pages[page_index].excluded:
        pixmap = _excluded_overlay(pixmap)
    return QIcon(pixmap)


def load_preview_pixmap(manifest: Manifest, page_index: int) -> QPixmap:
    """The "normal view" preview: current effective state, at full
    resolution - ImagePreviewLabel handles fit/zoom display scaling, and
    keeping the true pixels available is what makes zooming in for detail
    actually show detail instead of upscaled blur."""
    data = load_page_image_bytes(manifest, page_index)
    pixmap = QPixmap()
    pixmap.loadFromData(data)
    if manifest.pages[page_index].excluded:
        pixmap = _excluded_overlay(pixmap)
    return pixmap


def load_original_pixmap_and_size(
    manifest: Manifest, page_index: int
) -> tuple[QPixmap, tuple[int, int]]:
    """The untouched original, for the crop/split tool: a full-resolution
    QPixmap plus its (width, height) - crop_box must be stored in the true
    resolution's coordinates, and zooming in needs the real pixels to be
    useful for precise placement."""
    data = load_page_bytes(manifest, page_index)
    original_size = get_image_size(data)
    pixmap = QPixmap()
    pixmap.loadFromData(data)
    return pixmap, original_size
