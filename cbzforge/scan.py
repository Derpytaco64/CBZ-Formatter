"""Phase 1: turn a source CBZ/ZIP or image directory into a best-guess Manifest.

Everything here is mechanical and fully automatic. It must never invent data
it can't support from the source itself (Step 3/3B in the original workflow
doc are explicitly human/GUI judgment calls, not something this module should
try to resolve). Guesses are marked unconfirmed and left for GUI review.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from cbzforge.manifest import ChapterBoundary, CorruptionReport, Manifest, Page

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# Official digital release style, e.g.:
# "Kagurabachi - c001 (v01) - p000 [Cover] [dig] [Mission] [VIZ Media] [LuCaZ].jpg"
FLAT_DIGITAL_PATTERN = re.compile(
    r"^(?P<series>.+?) - c(?P<chapter>\d+(?:x\d+)?) \(v(?P<volume>\d+)\) - "
    r"p(?P<page>\d+(?:-\d+)?)"
    r"(?: \[(?P<special>[^\]]+)\])?"
    r" \[(?P<format>[^\]]+)\]"
    r" \[(?P<title>[^\]]+)\]"
    r" \[(?P<publisher>[^\]]+)\]"
    r" \[(?P<group>[^\]]+)\]"
    r"\.(?P<ext>\w+)$"
)

# Fan scanlation style folder name, e.g.:
# "Vol.01 Ch.0001 - Chapter Title (en) [Group]"
FOLDER_CHAPTER_PATTERN = re.compile(
    r"Ch\.?(?P<chapter>\d+(?:\.\d+)?)\s*-\s*(?P<title>.+?)\s*(?:\(|\[|$)"
)

# Minimum fraction of entries that must match the flat pattern before we
# trust it over the folder/fallback parsers.
FLAT_MATCH_THRESHOLD = 0.9


def _natural_sort_key(name: str) -> list:
    parts = re.split(r"(\d+)", name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def check_corruption(zip_path: str | Path) -> CorruptionReport:
    bad_entries: list[str] = []
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            try:
                z.read(info.filename)
            except (zipfile.BadZipFile, RuntimeError, OSError):
                bad_entries.append(info.filename)
    return CorruptionReport(clean=not bad_entries, bad_entries=bad_entries)


def _list_zip_images(zip_path: str | Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as z:
        names = [
            n
            for n in z.namelist()
            if not n.endswith("/")
            and Path(n).suffix.lower() in IMAGE_EXTENSIONS
        ]
    return sorted(names, key=_natural_sort_key)


def _list_dir_images(dir_path: str | Path) -> list[str]:
    root = Path(dir_path)
    names = [
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(names, key=_natural_sort_key)


def _parse_flat_digital(names: list[str]) -> list[Page] | None:
    matches = [FLAT_DIGITAL_PATTERN.match(Path(n).name) for n in names]
    hit_rate = sum(m is not None for m in matches) / len(names) if names else 0
    if hit_rate < FLAT_MATCH_THRESHOLD:
        return None

    pages = []
    for index, (name, m) in enumerate(zip(names, matches)):
        if m is None:
            pages.append(Page(index=index, source_name=name))
            continue
        gd = m.groupdict()
        pages.append(
            Page(
                index=index,
                source_name=name,
                chapter_number=gd["chapter"],
                chapter_title=gd["title"],
                special_tag=gd["special"],
            )
        )
    return pages


def _parse_folder_per_chapter(names: list[str]) -> list[Page] | None:
    if not any("/" in n for n in names):
        return None

    pages = []
    for index, name in enumerate(names):
        folder = Path(name).parent.name
        m = FOLDER_CHAPTER_PATTERN.search(folder)
        chapter_number = m.group("chapter") if m else None
        chapter_title = m.group("title").strip() if m else None
        pages.append(
            Page(
                index=index,
                source_name=name,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
            )
        )
    return pages


def _parse_fallback(names: list[str]) -> list[Page]:
    return [Page(index=i, source_name=n) for i, n in enumerate(names)]


def _derive_chapter_boundaries(pages: list[Page]) -> list[ChapterBoundary]:
    boundaries: list[ChapterBoundary] = []
    last_chapter: str | None = object()  # sentinel, never equals a real value
    for page in pages:
        if page.chapter_number is None:
            continue
        if page.chapter_number != last_chapter:
            boundaries.append(
                ChapterBoundary(
                    page_index=page.index,
                    chapter_number=page.chapter_number,
                    chapter_title=page.chapter_title,
                    confirmed=False,
                )
            )
            last_chapter = page.chapter_number
    return boundaries


def build_manifest(source: str | Path) -> Manifest:
    source = Path(source)
    is_zip = source.is_file() and source.suffix.lower() in {".cbz", ".zip"}

    corruption = None
    if is_zip:
        corruption = check_corruption(source)
        names = _list_zip_images(source)
    else:
        names = _list_dir_images(source)

    parser_used = "fallback"
    pages = _parse_flat_digital(names)
    if pages is not None:
        parser_used = "flat_digital"
    else:
        pages = _parse_folder_per_chapter(names)
        if pages is not None:
            parser_used = "folder_per_chapter"
        else:
            pages = _parse_fallback(names)

    chapter_boundaries = _derive_chapter_boundaries(pages)

    return Manifest(
        source=str(source),
        page_count=len(pages),
        parser_used=parser_used,
        pages=pages,
        chapter_boundaries=chapter_boundaries,
        front_cover_index=0 if pages else None,
        corruption=corruption,
    )
