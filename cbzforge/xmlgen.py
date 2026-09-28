"""Phase 2 (part 1): generate and validate ComicInfo.xml from a confirmed Manifest.

Schema mirrors the reference tagged example (Kagurabachi Vol. 1): flat field
list at the root, followed by a full <Pages> block. Never called on an
unconfirmed manifest - the GUI is responsible for resolving `bookmarks`
before this runs.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional

from cbzforge.manifest import Manifest
from cbzforge.page_ops import resolve_output_pages

STORY_BOOKMARK_RE = re.compile(r"^Chapter \d+(\.\d+)?(: .+)?$")

XML_DECLARATION = '<?xml version="1.0"?>\n'


@dataclass
class SeriesMetadata:
    series: str
    volume: int
    number: str
    title: str
    summary: str = ""
    notes: str = ""
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    writer: str = ""
    penciller: str = ""
    inker: str = ""
    cover_artist: str = ""
    publisher: str = ""
    imprint: str = ""
    genre: str = ""
    language_iso: str = "en"
    age_rating: str = ""
    black_and_white: str = "Yes"
    manga: str = "YesAndRightToLeft"


_FIELD_ORDER = (
    ("Title", "title"),
    ("Series", "series"),
    ("Number", "number"),
    ("Volume", "volume"),
    ("Summary", "summary"),
    ("Notes", "notes"),
    ("Year", "year"),
    ("Month", "month"),
    ("Day", "day"),
    ("Writer", "writer"),
    ("Penciller", "penciller"),
    ("Inker", "inker"),
    ("CoverArtist", "cover_artist"),
    ("Publisher", "publisher"),
    ("Imprint", "imprint"),
    ("Genre", "genre"),
)

_FIELD_ORDER_AFTER_PAGECOUNT = (
    ("LanguageISO", "language_iso"),
    ("AgeRating", "age_rating"),
    ("BlackAndWhite", "black_and_white"),
    ("Manga", "manga"),
)


def build_comicinfo_xml(manifest: Manifest, meta: SeriesMetadata) -> str:
    if not manifest.bookmarks:
        raise ValueError(
            "manifest has no resolved bookmarks - run bookmarks.suggest_bookmarks() "
            "and confirm placement via the GUI before generating ComicInfo.xml"
        )

    output_pages = resolve_output_pages(manifest)
    if not output_pages:
        raise ValueError("every page is excluded - nothing left to build")
    output_indices = {p.index for p in output_pages}

    seen_indices: dict[int, str] = {}
    for bookmark in manifest.bookmarks:
        if bookmark.page_index not in output_indices:
            raise ValueError(
                f"bookmark {bookmark.label!r} points at page {bookmark.page_index}, "
                f"which is excluded from the build - move the bookmark or re-include "
                f"the page in the GUI before building"
            )
        if bookmark.page_index in seen_indices:
            raise ValueError(
                f"page {bookmark.page_index} has more than one bookmark "
                f"({seen_indices[bookmark.page_index]!r} and {bookmark.label!r}) - "
                f"resolve the collision (move one) in the GUI before building"
            )
        seen_indices[bookmark.page_index] = bookmark.label

    root = ET.Element(
        "ComicInfo",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
        },
    )

    def add(tag: str, value) -> None:
        if value in (None, ""):
            return
        el = ET.SubElement(root, tag)
        el.text = str(value)

    for tag, attr in _FIELD_ORDER:
        add(tag, getattr(meta, attr))

    add("PageCount", len(output_pages))

    for tag, attr in _FIELD_ORDER_AFTER_PAGECOUNT:
        add(tag, getattr(meta, attr))

    pages_el = ET.SubElement(root, "Pages")
    bookmark_by_index = {b.page_index: b for b in manifest.bookmarks}
    for output_position, page in enumerate(output_pages):
        attrib = {"Image": str(output_position)}
        bookmark = bookmark_by_index.get(page.index)
        if bookmark is not None:
            attrib["Type"] = bookmark.type
            attrib["Bookmark"] = bookmark.label
        ET.SubElement(pages_el, "Page", attrib)

    body = ET.tostring(root, encoding="unicode")
    return XML_DECLARATION + body


def validate_comicinfo_xml(xml_text: str, expected_page_count: int) -> list[str]:
    """Return a list of validation errors; empty list means valid."""
    errors: list[str] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return [f"not well-formed XML: {exc}"]

    if root.tag != "ComicInfo":
        errors.append(f"root element is <{root.tag}>, expected <ComicInfo>")

    page_count_el = root.find("PageCount")
    if page_count_el is None or page_count_el.text is None:
        errors.append("missing <PageCount>")
    elif int(page_count_el.text) != expected_page_count:
        errors.append(
            f"<PageCount> is {page_count_el.text}, expected {expected_page_count}"
        )

    pages_el = root.find("Pages")
    page_elements = pages_el.findall("Page") if pages_el is not None else []
    if pages_el is None:
        errors.append("missing <Pages> block")
    elif len(page_elements) != expected_page_count:
        errors.append(
            f"<Pages> has {len(page_elements)} <Page> elements, expected {expected_page_count}"
        )

    for page_el in page_elements:
        if page_el.get("Type") != "Story":
            continue
        bookmark = page_el.get("Bookmark", "")
        if not STORY_BOOKMARK_RE.match(bookmark):
            errors.append(
                f"Image={page_el.get('Image')}: Story bookmark {bookmark!r} does not "
                f"match 'Chapter N: Title' / 'Chapter N'"
            )

    return errors
