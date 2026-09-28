"""Tests for page reordering and the crop/split/exclude/undo editing tools
wired into MainWindow. Real drag-and-drop gestures can't be simulated
headlessly (same limitation noted in test_gui.py for bookmark placement),
so page-reorder-via-drag is tested at the signal level: emitting
pageMoveRequested is exactly what PageThumbnailGrid.dropEvent does once it
has decided a page (not a bookmark) was dropped - the Qt-internals part of
that decision is simple mime-type dispatch mirroring the already-used
BookmarkListWidget/bookmarkDropped pattern.
"""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from cbzforge.gui.main_window import MainWindow
from cbzforge.manifest import Bookmark

FIXTURE = str(
    Path(__file__).parent.parent
    / "Kagurabachi Vol. 1 - Takeru Hokazono.cbz"
)


def _qapp():
    return QApplication.instance() or QApplication([])


def _window():
    window = MainWindow()
    window._load_source(FIXTURE)
    return window


def _drag_on_preview(label, start: QPoint, end: QPoint) -> None:
    QTest.mousePress(label, Qt.LeftButton, pos=start)
    QTest.mouseMove(label, end)
    QTest.mouseRelease(label, Qt.LeftButton, pos=end)


# ---- page reordering -------------------------------------------------------


def test_move_selected_page_to_position_updates_order_and_selection():
    _qapp()
    window = _window()
    moved_source_name = window.manifest.pages[10].source_name

    window.thumbnail_grid.setCurrentRow(10)
    window.move_to_spin.setValue(3)
    window._move_selected_page_to_position()

    assert window.manifest.pages[3].source_name == moved_source_name
    assert [p.index for p in window.manifest.pages] == list(range(202))
    assert window._selected_page_index() == 3


def test_drag_reorder_signal_moves_page_and_carries_bookmark():
    _qapp()
    window = _window()
    # bookmark currently on page 20 should follow that page's content to
    # its new position, same guarantee page_ops.move_page already proved
    window.manifest.bookmarks.append(
        Bookmark(page_index=20, type="Other", label="Test Marker")
    )
    moved_source_name = window.manifest.pages[20].source_name

    window.thumbnail_grid.pageMoveRequested.emit(20, 5)

    assert window.manifest.pages[5].source_name == moved_source_name
    marker = next(b for b in window.manifest.bookmarks if b.label == "Test Marker")
    assert marker.page_index == 5


def test_move_page_is_undoable():
    _qapp()
    window = _window()
    original_order = [p.source_name for p in window.manifest.pages]

    window.thumbnail_grid.pageMoveRequested.emit(15, 2)
    assert [p.source_name for p in window.manifest.pages] != original_order

    window._undo()
    assert [p.source_name for p in window.manifest.pages] == original_order


# ---- exclude ----------------------------------------------------------------


def test_exclude_toggle_marks_page_and_skips_it_from_output():
    from cbzforge.page_ops import resolve_output_pages

    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)

    window.exclude_btn.setChecked(True)

    assert window.manifest.pages[6].excluded is True
    assert 6 not in {p.index for p in resolve_output_pages(window.manifest)}

    window.exclude_btn.setChecked(False)
    assert window.manifest.pages[6].excluded is False


def test_exclude_is_undoable():
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)
    window.exclude_btn.setChecked(True)
    assert window.manifest.pages[6].excluded is True

    window._undo()
    assert window.manifest.pages[6].excluded is False


def test_selecting_a_different_page_syncs_exclude_button_without_side_effects():
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)
    window.exclude_btn.setChecked(True)

    # selecting an unrelated, non-excluded page must not flip page 6 back
    window.thumbnail_grid.setCurrentRow(7)
    assert window.exclude_btn.isChecked() is False
    assert window.manifest.pages[6].excluded is True
    assert window.manifest.pages[7].excluded is False


# ---- crop / split -----------------------------------------------------------


def test_crop_tool_applies_crop_box_to_selected_page():
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)

    window.crop_btn.setChecked(True)
    assert window.preview_label.selection_mode == "crop"

    _drag_on_preview(window.preview_label, QPoint(10, 10), QPoint(60, 80))
    window._apply_edit_selection()

    assert window.manifest.pages[6].crop_box is not None
    assert window.crop_btn.isChecked() is False  # tool auto-deactivates after apply


def test_crop_is_undoable():
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)
    window.crop_btn.setChecked(True)
    _drag_on_preview(window.preview_label, QPoint(10, 10), QPoint(60, 80))
    window._apply_edit_selection()
    assert window.manifest.pages[6].crop_box is not None

    window._undo()
    assert window.manifest.pages[6].crop_box is None


def test_reset_crop_clears_without_full_undo():
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)
    window.crop_btn.setChecked(True)
    _drag_on_preview(window.preview_label, QPoint(10, 10), QPoint(60, 80))
    window._apply_edit_selection()
    assert window.manifest.pages[6].crop_box is not None

    window.thumbnail_grid.setCurrentRow(6)
    window._reset_crop_selected()
    assert window.manifest.pages[6].crop_box is None


def test_split_tool_with_two_regions_creates_extra_page():
    _qapp()
    window = _window()
    original_count = len(window.manifest.pages)
    window.thumbnail_grid.setCurrentRow(6)

    window.split_btn.setChecked(True)
    assert window.preview_label.selection_mode == "split"
    # Comfortable gap between the two regions so the second drag's start
    # point isn't mistaken for a resize-handle grab on the first rectangle
    # (rectangles are now directly editable via corner/edge handles).
    _drag_on_preview(window.preview_label, QPoint(5, 5), QPoint(35, 90))
    _drag_on_preview(window.preview_label, QPoint(55, 5), QPoint(85, 90))

    window._apply_edit_selection()

    assert len(window.manifest.pages) == original_count + 1
    assert window.manifest.pages[6].crop_box is not None
    assert window.manifest.pages[7].crop_box is not None
    assert window.manifest.pages[6].source_name == window.manifest.pages[7].source_name


def test_split_with_single_region_asks_for_confirmation(monkeypatch):
    _qapp()
    window = _window()
    original_count = len(window.manifest.pages)
    window.thumbnail_grid.setCurrentRow(6)
    window.split_btn.setChecked(True)
    _drag_on_preview(window.preview_label, QPoint(5, 5), QPoint(40, 90))

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    window._apply_edit_selection()

    assert len(window.manifest.pages) == original_count  # single-region split == plain crop
    assert window.manifest.pages[6].crop_box is not None


def test_apply_with_no_selection_shows_message_and_does_nothing(monkeypatch):
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)
    window.crop_btn.setChecked(True)

    info_calls = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: info_calls.append(a) or QMessageBox.Ok
    )
    window._apply_edit_selection()

    assert len(info_calls) == 1
    assert window.manifest.pages[6].crop_box is None


def test_crop_mode_reentry_prepopulates_existing_crop_box():
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)
    window.crop_btn.setChecked(True)
    _drag_on_preview(window.preview_label, QPoint(10, 10), QPoint(60, 80))
    window._apply_edit_selection()
    assert window.manifest.pages[6].crop_box is not None

    # Leaving and re-entering crop mode should show the existing crop as an
    # adjustable box, not force the user to redraw it from scratch.
    window.crop_btn.setChecked(True)
    assert window.preview_label.has_selection()
    assert len(window.preview_label.get_source_rects()) == 1


def test_resize_handle_drag_adjusts_crop_before_apply():
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)
    window.crop_btn.setChecked(True)
    _drag_on_preview(window.preview_label, QPoint(10, 10), QPoint(60, 80))
    first_rect = window.preview_label.get_source_rects()[0]

    # drag the bottom-right handle of the just-drawn rectangle further out
    _drag_on_preview(window.preview_label, QPoint(60, 80), QPoint(80, 100))
    resized_rect = window.preview_label.get_source_rects()[0]

    assert resized_rect[2] > first_rect[2]
    assert resized_rect[3] > first_rect[3]

    window._apply_edit_selection()
    assert window.manifest.pages[6].crop_box == resized_rect


def test_zoom_in_button_updates_zoom_label():
    _qapp()
    window = _window()
    window.thumbnail_grid.setCurrentRow(6)
    initial = window.zoom_label.text()

    window.preview_label.zoom_in()

    assert window.zoom_label.text() != initial
