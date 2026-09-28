from cbzforge.manifest import Bookmark, Manifest, Page
from cbzforge.page_ops import (
    build_output_index_map,
    move_page,
    reset_crop,
    resolve_output_pages,
    set_excluded,
    split_page,
)


def _make_manifest(n: int) -> Manifest:
    pages = [Page(index=i, source_name=f"{i:03d}.jpg") for i in range(n)]
    return Manifest(source="fake.cbz", page_count=n, pages=pages)


def _indices_and_names(manifest: Manifest) -> list[tuple[int, str]]:
    return [(p.index, p.source_name) for p in manifest.pages]


def test_move_page_forward_renumbers_gaplessly():
    m = _make_manifest(5)
    move_page(m, 1, 3)
    # source_name "001" moved from position 1 to 3
    assert [p.source_name for p in m.pages] == ["000.jpg", "002.jpg", "003.jpg", "001.jpg", "004.jpg"]
    assert [p.index for p in m.pages] == [0, 1, 2, 3, 4]


def test_move_page_backward_renumbers_gaplessly():
    m = _make_manifest(5)
    move_page(m, 3, 1)
    assert [p.source_name for p in m.pages] == ["000.jpg", "003.jpg", "001.jpg", "002.jpg", "004.jpg"]
    assert [p.index for p in m.pages] == [0, 1, 2, 3, 4]


def test_move_page_bookmark_follows_the_page_not_the_index():
    m = _make_manifest(5)
    m.bookmarks = [Bookmark(page_index=1, type="Story", label="Chapter 1")]
    move_page(m, 1, 3)
    # page "001.jpg" is now at index 3 - the bookmark must follow it there
    assert m.bookmarks[0].page_index == 3
    assert m.pages[3].source_name == "001.jpg"


def test_move_page_noop_when_same_index():
    m = _make_manifest(5)
    original = _indices_and_names(m)
    move_page(m, 2, 2)
    assert _indices_and_names(m) == original


def test_set_excluded_toggle_and_output_resolution():
    m = _make_manifest(5)
    set_excluded(m, 2, True)
    assert m.pages[2].excluded is True
    # page stays in the manifest list, just skipped from output
    assert len(m.pages) == 5
    output = resolve_output_pages(m)
    assert [p.source_name for p in output] == ["000.jpg", "001.jpg", "003.jpg", "004.jpg"]

    set_excluded(m, 2, False)
    assert resolve_output_pages(m) == m.pages


def test_build_output_index_map_skips_excluded_and_stays_gapless():
    m = _make_manifest(5)
    set_excluded(m, 1, True)
    set_excluded(m, 3, True)
    index_map = build_output_index_map(m)
    # only non-excluded pages (0, 2, 4) appear, renumbered 0, 1, 2
    assert index_map == {0: 0, 2: 1, 4: 2}


def test_reset_crop_clears_crop_box():
    m = _make_manifest(3)
    m.pages[1].crop_box = [10, 10, 50, 50]
    reset_crop(m, 1)
    assert m.pages[1].crop_box is None


def test_split_page_replaces_one_page_with_n_pieces():
    m = _make_manifest(3)
    m.pages[1].chapter_number = "5"
    m.pages[1].chapter_title = "Some Chapter"
    split_page(m, 1, [[0, 0, 50, 50], [50, 0, 100, 50]])

    assert len(m.pages) == 4  # 3 - 1 + 2
    assert [p.index for p in m.pages] == [0, 1, 2, 3]
    # both pieces share the original source image
    assert m.pages[1].source_name == "001.jpg"
    assert m.pages[2].source_name == "001.jpg"
    assert m.pages[1].crop_box == [0, 0, 50, 50]
    assert m.pages[2].crop_box == [50, 0, 100, 50]
    # metadata carried over to both pieces
    assert m.pages[1].chapter_number == "5"
    assert m.pages[2].chapter_number == "5"
    # unaffected pages renumbered correctly
    assert m.pages[0].source_name == "000.jpg"
    assert m.pages[3].source_name == "002.jpg"


def test_split_page_bookmark_moves_to_first_piece():
    m = _make_manifest(3)
    m.bookmarks = [Bookmark(page_index=1, type="Story", label="Chapter 1")]
    split_page(m, 1, [[0, 0, 50, 50], [50, 0, 100, 50], [0, 50, 100, 100]])

    assert m.bookmarks[0].page_index == 1  # first of the 3 new pieces
    assert m.pages[1].crop_box == [0, 0, 50, 50]


def test_split_page_with_single_box_still_works():
    m = _make_manifest(2)
    split_page(m, 0, [[0, 0, 10, 10]])
    assert len(m.pages) == 2
    assert m.pages[0].crop_box == [0, 0, 10, 10]
