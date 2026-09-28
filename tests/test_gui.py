"""Headless smoke tests for the GUI (QT_QPA_PLATFORM=offscreen, set in
conftest.py). Real drag-and-drop gestures can't be simulated headlessly, so
these call the same methods a drop event would call (move_bookmark etc.)
directly - that's the actual data-mutation logic; the Qt drop handler in
widgets.py is a thin adapter on top of it.
"""
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from cbzforge.gui.main_window import MainWindow

FIXTURE = str(
    Path(__file__).parent.parent
    / "Kagurabachi Vol. 1 - Takeru Hokazono.cbz"
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    return MainWindow()


def test_load_source_populates_thumbnails_and_bookmarks(window):
    window._load_source(FIXTURE)

    assert window.manifest is not None
    assert window.manifest.page_count == 202
    assert window.thumbnail_grid.count() == 202
    assert window.bookmark_list.count() == len(window.manifest.bookmarks)
    assert window.bookmark_list.count() > 0


def test_move_bookmark_updates_manifest_and_refreshes_list(window):
    window._load_source(FIXTURE)

    chapter_one_index = next(
        i
        for i, b in enumerate(window.manifest.bookmarks)
        if b.type == "Story" and b.label.startswith("Chapter 1:")
    )
    assert window.manifest.bookmarks[chapter_one_index].page_index == 0

    window.move_bookmark(chapter_one_index, 5)

    assert window.manifest.bookmarks[chapter_one_index].page_index == 5
    # list row text reflects the new page index
    row_texts = [window.bookmark_list.item(i).text() for i in range(window.bookmark_list.count())]
    assert any(text.startswith("[5]") and "Chapter 1" in text for text in row_texts)


def test_add_and_remove_bookmark(window):
    window._load_source(FIXTURE)
    initial_count = len(window.manifest.bookmarks)

    from cbzforge.manifest import Bookmark

    window.manifest.bookmarks.append(Bookmark(page_index=10, type="Other", label="Test"))
    window._refresh_bookmark_list()
    assert len(window.manifest.bookmarks) == initial_count + 1
    assert window.bookmark_list.count() == initial_count + 1

    del window.manifest.bookmarks[-1]
    window._refresh_bookmark_list()
    assert len(window.manifest.bookmarks) == initial_count


def test_gather_metadata_reflects_form_fields(window):
    window._load_source(FIXTURE)
    window.field_series.setText("Kagurabachi")
    window.field_volume.setValue(1)
    window.field_number.setText("1.0")
    window.field_title.setText("Kagurabachi Vol. 1")
    window.field_writer.setText("Takeru Hokazono")

    meta = window._gather_metadata()
    assert meta.series == "Kagurabachi"
    assert meta.volume == 1
    assert meta.title == "Kagurabachi Vol. 1"
    assert meta.writer == "Takeru Hokazono"


def test_build_end_to_end_via_gui_state(window, tmp_path):
    from cbzforge.pack import build_cbz
    from cbzforge.xmlgen import build_comicinfo_xml, validate_comicinfo_xml

    window._load_source(FIXTURE)

    # Resolve the same front-cover/chapter-1 collision as the pipeline test -
    # this is exactly what dragging the bookmark in the real GUI would do.
    chapter_one = next(
        b for b in window.manifest.bookmarks if b.type == "Story" and b.label.startswith("Chapter 1:")
    )
    chapter_one.page_index = 5

    window.field_series.setText("Kagurabachi")
    window.field_volume.setValue(1)
    window.field_number.setText("1.0")
    window.field_title.setText("Kagurabachi Vol. 1")
    window.field_writer.setText("Takeru Hokazono")
    window.field_publisher.setText("VIZ Media")
    window.field_imprint.setText("SHONEN JUMP")

    meta = window._gather_metadata()
    xml_text = build_comicinfo_xml(window.manifest, meta)
    assert validate_comicinfo_xml(xml_text, window.manifest.page_count) == []

    output_path = tmp_path / "out.cbz"
    build_cbz(window.manifest, xml_text, output_path)
    try:
        assert output_path.exists()
    finally:
        # see the matching note in test_build_pipeline.py - tmp_path is on a
        # small tmpfs here, don't let ~225MB outputs accumulate across runs.
        output_path.unlink(missing_ok=True)
