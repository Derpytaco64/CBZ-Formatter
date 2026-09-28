"""Manifest schema shared by the scan, GUI review, and build stages.

The manifest is the single artifact that flows through the whole pipeline:
`scan` produces one with best-guess chapter boundaries, the GUI lets the user
move/confirm those boundaries and edit metadata, and `build` consumes the
confirmed manifest to produce the final CBZ. Nothing downstream of scan should
ever re-derive page order or chapter guesses from the source archive again —
the manifest is the source of truth once it exists.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Page:
    """One entry in Manifest.pages.

    INVARIANT: `index` always equals this page's position in
    Manifest.pages (list order == index order). Every operation that
    reorders/inserts/removes pages (cbzforge.page_ops) must renumber every
    page's `.index` afterwards so `manifest.pages[i].index == i` always
    holds - several call sites (thumbnails.py, pack.py) index directly by
    position and rely on this.

    `excluded` and `crop_box` are non-destructive edits: the original
    source bytes are never touched. `excluded=True` means this page is
    skipped when building the final CBZ (but stays visible/re-includable
    in the editor). `crop_box`, if set, is (left, top, right, bottom) in
    the *original* image's pixel coordinates - clearing it back to None is
    the "undo" for a crop. A page produced by splitting another page keeps
    the same `source_name` as its sibling pieces, distinguished only by
    `crop_box` - so `source_name` is NOT a unique key once splits exist;
    use object identity or `index` instead.
    """

    index: int
    source_name: str
    chapter_number: Optional[str] = None
    chapter_title: Optional[str] = None
    special_tag: Optional[str] = None
    excluded: bool = False
    crop_box: Optional[list[int]] = None


@dataclass
class ChapterBoundary:
    page_index: int
    chapter_number: Optional[str]
    chapter_title: Optional[str]
    confirmed: bool = False


@dataclass
class CorruptionReport:
    clean: bool
    bad_entries: list[str] = field(default_factory=list)


@dataclass
class Bookmark:
    """A resolved ComicInfo <Page> bookmark: one entry per notable page.

    `type` matches the ComicInfo Page Type attribute (FrontCover, Story,
    Other, Editorial, BackCover). `label` matches the Bookmark attribute,
    e.g. "Chapter 6: Peace" or the fixed labels ("Cover", "Bonus Content").
    This is the resolved, human-confirmed placement - distinct from the
    scan stage's raw ChapterBoundary guesses.
    """

    page_index: int
    type: str
    label: str


@dataclass
class Manifest:
    source: str
    page_count: int
    parser_used: str = "fallback"
    pages: list[Page] = field(default_factory=list)
    chapter_boundaries: list[ChapterBoundary] = field(default_factory=list)
    bookmarks: list[Bookmark] = field(default_factory=list)
    front_cover_index: Optional[int] = None
    back_cover_index: Optional[int] = None
    corruption: Optional[CorruptionReport] = None
    series_profile: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        data = dict(data)
        data["pages"] = [Page(**p) for p in data.get("pages", [])]
        data["chapter_boundaries"] = [
            ChapterBoundary(**c) for c in data.get("chapter_boundaries", [])
        ]
        data["bookmarks"] = [Bookmark(**b) for b in data.get("bookmarks", [])]
        if data.get("corruption") is not None:
            data["corruption"] = CorruptionReport(**data["corruption"])
        return cls(**data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True)
        )

    @classmethod
    def load(cls, path: str | Path) -> "Manifest":
        data = yaml.safe_load(Path(path).read_text())
        return cls.from_dict(data)
