"""Turn scan.py's raw chapter-number groups into candidate Bookmarks.

This is a *suggestion* layer, same status as metadata_sources.py and ocr.py -
its output is meant to pre-fill the GUI, not to be trusted and packaged
as-is. A chapter-number group is not automatically a real "Story" chapter:
e.g. a "008x1" group with an [Afterword] special tag is bonus/omake content,
not "Chapter 8.1", and only a human (via the GUI) can reliably tell the
difference in the general case.
"""
from __future__ import annotations

from cbzforge.manifest import Bookmark, Manifest, Page

SPECIAL_TAG_BOOKMARKS = {
    "ToC": ("Other", "Table of Contents"),
    "Characters": ("Other", "Characters"),
}


def _normalize_chapter_number(raw: str) -> str:
    """"008" -> "8", "5.5" -> "5.5", leading zeros stripped but suffixes kept."""
    digits = raw.lstrip("0") or "0"
    return digits


def _is_bonus_group(chapter_number: str, pages: list[Page]) -> bool:
    if "x" in chapter_number.lower():
        return True
    return any(p.special_tag == "Afterword" for p in pages if p.chapter_number == chapter_number)


def suggest_bookmarks(manifest: Manifest) -> list[Bookmark]:
    suggestions: list[Bookmark] = []

    if manifest.front_cover_index is not None:
        suggestions.append(
            Bookmark(page_index=manifest.front_cover_index, type="FrontCover", label="Cover")
        )

    for page in manifest.pages:
        if page.special_tag in SPECIAL_TAG_BOOKMARKS:
            bmk_type, label = SPECIAL_TAG_BOOKMARKS[page.special_tag]
            suggestions.append(Bookmark(page_index=page.index, type=bmk_type, label=label))

    pages_by_chapter: dict[str, list[Page]] = {}
    for page in manifest.pages:
        if page.chapter_number is not None:
            pages_by_chapter.setdefault(page.chapter_number, []).append(page)

    for boundary in manifest.chapter_boundaries:
        group_pages = pages_by_chapter.get(boundary.chapter_number, [])
        if boundary.chapter_number is not None and _is_bonus_group(boundary.chapter_number, group_pages):
            suggestions.append(
                Bookmark(page_index=boundary.page_index, type="Other", label="Bonus Content")
            )
            continue

        number = _normalize_chapter_number(boundary.chapter_number) if boundary.chapter_number else "?"
        label = f"Chapter {number}: {boundary.chapter_title}" if boundary.chapter_title else f"Chapter {number}"
        suggestions.append(Bookmark(page_index=boundary.page_index, type="Story", label=label))

    if manifest.back_cover_index is not None:
        suggestions.append(
            Bookmark(page_index=manifest.back_cover_index, type="BackCover", label="Back Cover")
        )

    suggestions.sort(key=lambda b: b.page_index)
    return suggestions
