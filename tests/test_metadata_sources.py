"""Live smoke tests against the real MangaDex/AniList APIs.

These hit the network deliberately - the goal is to catch response-shape
drift in the parsing logic, not just exercise mocks. Marked `network` so a
fully offline test run can deselect them with `-m "not network"`.
"""
import pytest

from cbzforge.metadata_sources import (
    anilist_series_metadata,
    mangadex_chapter_titles,
    mangadex_search_manga,
)

pytestmark = pytest.mark.network


def test_mangadex_search_finds_kagurabachi():
    results = mangadex_search_manga("Kagurabachi", limit=5)
    assert results
    assert any("kagurabachi" in r["title"].lower() for r in results)


def test_mangadex_chapter_titles_returns_chapter_one():
    manga_id = mangadex_search_manga("Kagurabachi", limit=1)[0]["id"]
    chapters = mangadex_chapter_titles(manga_id)
    assert chapters
    assert all({"volume", "chapter", "title"} <= c.keys() for c in chapters)


def test_anilist_series_metadata_has_expected_shape():
    data = anilist_series_metadata("Kagurabachi")
    assert data is not None
    assert "kagurabachi" in data["title"].lower()
    assert isinstance(data["genres"], list)
    assert isinstance(data["staff"], dict)
