"""Mutating operations on a Manifest's page list: reordering, excluding,
cropping, splitting. Kept in one place because they all share the same
hazard - moving/inserting/removing pages must renumber every Page.index
afterwards (see the invariant documented on Page in manifest.py) and must
carry bookmarks along with the *page* they were placed on, not the index,
since indices shift under these operations.

Bookmark remapping uses Python object identity (id(page)), not
source_name or index, because a page produced by split_page() shares its
source_name with its siblings, and index is exactly what's changing.
"""
from __future__ import annotations

from cbzforge.manifest import Manifest, Page


def _renumber(pages: list[Page]) -> None:
    for i, page in enumerate(pages):
        page.index = i


def move_page(manifest: Manifest, old_index: int, new_index: int) -> None:
    """Move the page currently at old_index to new_index, shifting the rest.
    Bookmarks follow the page they were placed on."""
    pages = manifest.pages
    if old_index == new_index or not (0 <= old_index < len(pages)):
        return

    page_by_old_index = {p.index: p for p in pages}
    moved_page = page_by_old_index[old_index]
    bookmark_targets = [(b, page_by_old_index.get(b.page_index)) for b in manifest.bookmarks]

    pages.remove(moved_page)
    clamped_new_index = max(0, min(new_index, len(pages)))
    pages.insert(clamped_new_index, moved_page)
    _renumber(pages)

    for bookmark, target_page in bookmark_targets:
        if target_page is not None:
            bookmark.page_index = target_page.index


def set_excluded(manifest: Manifest, page_index: int, excluded: bool) -> None:
    """Toggle exclusion. The page stays in the list (re-includable) but is
    skipped by resolve_output_pages() / the final build. Does not touch
    bookmarks - a bookmark left pointing at an excluded page is caught as a
    build-time validation error (see xmlgen.build_comicinfo_xml), the same
    "never silently drop data" pattern used for bookmark collisions."""
    for page in manifest.pages:
        if page.index == page_index:
            page.excluded = excluded
            return


def reset_crop(manifest: Manifest, page_index: int) -> None:
    """Undo a crop: since crop_box is non-destructive, this just clears it."""
    for page in manifest.pages:
        if page.index == page_index:
            page.crop_box = None
            return


def apply_crop_box(manifest: Manifest, page_index: int, crop_box: list[int]) -> None:
    for page in manifest.pages:
        if page.index == page_index:
            page.crop_box = list(crop_box)
            return


def split_page(manifest: Manifest, page_index: int, crop_boxes: list[list[int]]) -> None:
    """Replace the page at page_index with len(crop_boxes) new pages, each
    the same source image cropped to one of the given regions, in the
    order given. A bookmark that was on the original page moves to the
    first resulting piece - a 1-to-N split is inherently ambiguous about
    which piece "is" the bookmarked one, so the caller (GUI) should tell
    the user to double check bookmark placement after a split."""
    pages = manifest.pages
    if not crop_boxes:
        return

    page_by_index = {p.index: p for p in pages}
    original = page_by_index.get(page_index)
    if original is None:
        return
    pos = pages.index(original)

    new_pages = [
        Page(
            index=0,  # reassigned by _renumber below
            source_name=original.source_name,
            chapter_number=original.chapter_number,
            chapter_title=original.chapter_title,
            special_tag=original.special_tag if i == 0 else None,
            excluded=False,
            crop_box=list(box),
        )
        for i, box in enumerate(crop_boxes)
    ]

    affected_bookmarks = [b for b in manifest.bookmarks if b.page_index == page_index]

    pages[pos : pos + 1] = new_pages
    _renumber(pages)

    for bookmark in affected_bookmarks:
        bookmark.page_index = pos


def resolve_output_pages(manifest: Manifest) -> list[Page]:
    """Pages that will actually be written to the built CBZ, in order."""
    return [p for p in manifest.pages if not p.excluded]


def build_output_index_map(manifest: Manifest) -> dict[int, int]:
    """Maps a page's manifest-editing-order index to its position in the
    built CBZ (which skips excluded pages and is therefore gapless even
    when the manifest has exclusions)."""
    return {p.index: i for i, p in enumerate(resolve_output_pages(manifest))}
