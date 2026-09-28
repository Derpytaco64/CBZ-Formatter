"""Tests for the page preview panel and the right-side scroll area."""
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from cbzforge.gui.main_window import MainWindow

FIXTURE = str(
    Path(__file__).parent.parent
    / "Kagurabachi Vol. 1 - Takeru Hokazono.cbz"
)


def _qapp():
    return QApplication.instance() or QApplication([])


def test_selecting_a_page_populates_preview():
    _qapp()
    window = MainWindow()
    window._load_source(FIXTURE)

    assert window.preview_header.text() == "No page selected"
    assert window.preview_label._source_pixmap is None

    window.thumbnail_grid.setCurrentRow(5)

    assert "Page 5" in window.preview_header.text()
    assert window.preview_label._source_pixmap is not None
    assert not window.preview_label._source_pixmap.isNull()
    # the label's own displayed pixmap is the scaled-to-fit version, set via
    # the same code path resizeEvent uses
    assert window.preview_label.pixmap() is not None


def test_loading_a_new_source_resets_preview():
    _qapp()
    window = MainWindow()
    window._load_source(FIXTURE)
    window.thumbnail_grid.setCurrentRow(5)
    assert window.preview_label._source_pixmap is not None

    window._load_source(FIXTURE)

    assert window.preview_label._source_pixmap is None
    assert window.preview_header.text() == "No page selected"


def test_preview_rescales_on_resize():
    _qapp()
    window = MainWindow()
    window._load_source(FIXTURE)
    window.thumbnail_grid.setCurrentRow(0)

    first_size = window.preview_label.pixmap().size()
    # The preview now lives inside PreviewScrollArea (to support zoom/pan),
    # which reports the real viewport size via set_viewport_hint on every
    # resize - that's what actually drives "fit" rescaling now, since the
    # label's own size can be pinned to a manually-zoomed pixmap instead of
    # reflecting the space available.
    window.preview_label.set_viewport_hint(QSize(200, 200))
    second_size = window.preview_label.pixmap().size()

    assert second_size != first_size
    assert second_size.width() <= 200
    assert second_size.height() <= 200


def test_right_panel_scrolls_when_sections_exceed_viewport():
    _qapp()
    window = MainWindow()
    window.show()
    window.resize(1300, 300)  # deliberately too short for all sections at once
    QApplication.processEvents()

    scroll = window.right_scroll_area
    # widgetResizable=True means the content widget is only as wide as the
    # viewport but keeps its natural (taller) height, so the scrollbar
    # range should be > 0 whenever the expanded sections don't all fit.
    assert scroll.verticalScrollBar().maximum() > 0

    window.hide()
