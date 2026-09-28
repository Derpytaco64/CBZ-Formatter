"""Tests for the "Online Metadata Lookup" section, mocked so the suite stays
deterministic/offline - live behavior against the real APIs is covered by
tests/test_metadata_sources.py (network marker).
"""
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

import cbzforge.metadata_sources as metadata_sources
from cbzforge.gui.main_window import MainWindow, STATUS_COLOR_FAIL, STATUS_COLOR_OK
from cbzforge.gui.widgets import CollapsibleSection


def _qapp():
    return QApplication.instance() or QApplication([])


def test_collapsible_section_toggles_content_visibility():
    _qapp()
    content = QWidget()
    # isHidden() reflects the widget's own explicit flag; isVisible() is
    # ancestor-aware and would read False here regardless since this widget
    # tree is never .show()n (no real window in a headless test).
    section = CollapsibleSection("Title", content, expanded=True)
    assert content.isHidden() is False
    assert section.toggle_button.isChecked() is True

    section.toggle_button.setChecked(False)
    section._on_toggled(False)
    assert content.isHidden() is True

    section.toggle_button.setChecked(True)
    section._on_toggled(True)
    assert content.isHidden() is False


def test_lookup_requires_series_name(monkeypatch):
    _qapp()
    window = MainWindow()
    window.field_series.setText("")

    # QMessageBox.information() is modal and would block forever waiting for
    # a click that never comes under an automated/headless test - stub it
    # out and just confirm it was the one shown, not the real Qt dialog loop.
    info_calls = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: info_calls.append(a) or QMessageBox.Ok
    )

    called = {"mangadex": False}
    monkeypatch.setattr(
        metadata_sources, "mangadex_search_manga", lambda *a, **k: called.__setitem__("mangadex", True)
    )
    window._lookup_metadata_online()
    assert called["mangadex"] is False  # blocked before any lookup happened
    assert len(info_calls) == 1


def test_lookup_success_fills_empty_fields_and_shows_ok_status(monkeypatch):
    _qapp()
    window = MainWindow()
    window.field_series.setText("Kagurabachi")

    monkeypatch.setattr(
        metadata_sources,
        "mangadex_search_manga",
        lambda title, limit=5: [{"id": "abc123", "title": "Kagurabachi", "year": 2023}],
    )
    monkeypatch.setattr(
        metadata_sources,
        "mangadex_chapter_titles",
        lambda manga_id, language="en": [{"volume": "1", "chapter": "1", "title": "Mission"}],
    )
    monkeypatch.setattr(
        metadata_sources,
        "anilist_series_metadata",
        lambda title: {
            "title": "Kagurabachi",
            "description": "A swordsmith's <br>son seeks revenge.",
            "genres": ["Action", "Fantasy"],
            "staff": {"Story & Art": ["Takeru Hokazono"]},
            "start_date": {"year": 2023, "month": 9, "day": 18},
        },
    )

    window._lookup_metadata_online()

    assert "[OK]" in window.lookup_status_mangadex.text()
    assert "1 chapters" in window.lookup_status_mangadex.text()
    assert STATUS_COLOR_OK in window.lookup_status_mangadex.styleSheet()

    assert "[OK]" in window.lookup_status_anilist.text()
    assert STATUS_COLOR_OK in window.lookup_status_anilist.styleSheet()

    # empty fields got filled, HTML stripped
    assert "swordsmith's" in window.field_summary.toPlainText()
    assert "<br>" not in window.field_summary.toPlainText()
    assert window.field_genre.text() == "Action, Fantasy"
    assert "Story & Art: Takeru Hokazono" in window.lookup_staff_output.toPlainText()
    assert window._mangadex_chapters == [{"volume": "1", "chapter": "1", "title": "Mission"}]


def test_lookup_does_not_overwrite_already_filled_fields(monkeypatch):
    _qapp()
    window = MainWindow()
    window.field_series.setText("Kagurabachi")
    window.field_summary.setPlainText("my own summary")
    window.field_genre.setText("my own genre")

    monkeypatch.setattr(metadata_sources, "mangadex_search_manga", lambda title, limit=5: [])
    monkeypatch.setattr(
        metadata_sources,
        "anilist_series_metadata",
        lambda title: {
            "title": "Kagurabachi",
            "description": "should not appear",
            "genres": ["Action"],
            "staff": {},
            "start_date": None,
        },
    )

    window._lookup_metadata_online()

    assert window.field_summary.toPlainText() == "my own summary"
    assert window.field_genre.text() == "my own genre"
    assert "left as-is" in window.lookup_status_anilist.text()


def test_lookup_failure_shows_fail_status(monkeypatch):
    _qapp()
    window = MainWindow()
    window.field_series.setText("Some Unknown Series")

    monkeypatch.setattr(metadata_sources, "mangadex_search_manga", lambda title, limit=5: [])
    monkeypatch.setattr(metadata_sources, "anilist_series_metadata", lambda title: None)

    window._lookup_metadata_online()

    assert "[FAIL]" in window.lookup_status_mangadex.text()
    assert STATUS_COLOR_FAIL in window.lookup_status_mangadex.styleSheet()
    assert "[FAIL]" in window.lookup_status_anilist.text()
    assert STATUS_COLOR_FAIL in window.lookup_status_anilist.styleSheet()


def test_lookup_handles_exceptions_without_crashing(monkeypatch):
    _qapp()
    window = MainWindow()
    window.field_series.setText("Kagurabachi")

    def boom(*a, **k):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(metadata_sources, "mangadex_search_manga", boom)
    monkeypatch.setattr(metadata_sources, "anilist_series_metadata", boom)

    window._lookup_metadata_online()

    assert "[FAIL]" in window.lookup_status_mangadex.text()
    assert "network exploded" in window.lookup_status_mangadex.text()
    assert "[FAIL]" in window.lookup_status_anilist.text()
    assert "network exploded" in window.lookup_status_anilist.text()
