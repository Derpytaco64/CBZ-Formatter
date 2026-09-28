import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from cbzforge.bookmarks import suggest_bookmarks
from cbzforge.imaging import get_image_size
from cbzforge.page_ops import set_excluded, split_page
from cbzforge.pack import build_cbz
from cbzforge.scan import build_manifest
from cbzforge.xmlgen import SeriesMetadata, build_comicinfo_xml, validate_comicinfo_xml

FIXTURE = (
    Path(__file__).parent.parent
    / "Kagurabachi Vol. 1 - Takeru Hokazono.cbz"
)

META = SeriesMetadata(series="Kagurabachi", volume=1, number="1.0", title="Kagurabachi Vol. 1")


def _prepared_manifest():
    """Same baseline every page-editing test starts from: scan + suggested
    bookmarks + the front-cover/chapter-1 collision resolved (see
    test_build_pipeline.py for why that collision exists)."""
    manifest = build_manifest(FIXTURE)
    manifest.bookmarks = suggest_bookmarks(manifest)
    chapter_one = next(
        b for b in manifest.bookmarks if b.type == "Story" and b.label.startswith("Chapter 1:")
    )
    chapter_one.page_index = 5
    return manifest


def test_excluded_page_is_skipped_and_pagecount_shrinks(tmp_path):
    manifest = _prepared_manifest()
    original_count = manifest.page_count
    set_excluded(manifest, 6, True)  # a plain, non-bookmarked story page

    xml_text = build_comicinfo_xml(manifest, META)
    assert validate_comicinfo_xml(xml_text, original_count - 1) == []

    output_path = tmp_path / "out.cbz"
    build_cbz(manifest, xml_text, output_path)
    try:
        with zipfile.ZipFile(output_path) as z:
            image_names = [n for n in z.namelist() if n != "ComicInfo.xml"]
            assert len(image_names) == original_count - 1
    finally:
        output_path.unlink(missing_ok=True)


def test_bookmark_on_excluded_page_raises_clear_error():
    manifest = _prepared_manifest()
    set_excluded(manifest, 0, True)  # the Cover bookmark lives on page 0

    with pytest.raises(ValueError, match="excluded"):
        build_comicinfo_xml(manifest, META)


def test_crop_is_applied_to_the_built_image(tmp_path):
    manifest = _prepared_manifest()
    manifest.pages[6].crop_box = [0, 0, 100, 150]

    xml_text = build_comicinfo_xml(manifest, META)
    output_path = tmp_path / "out.cbz"
    build_cbz(manifest, xml_text, output_path)
    try:
        with zipfile.ZipFile(output_path) as z:
            image_names = sorted(n for n in z.namelist() if n != "ComicInfo.xml")
            cropped_bytes = z.read(image_names[6])
        assert get_image_size(cropped_bytes) == (100, 150)
    finally:
        output_path.unlink(missing_ok=True)


def test_split_page_produces_two_images_with_correct_crops(tmp_path):
    manifest = _prepared_manifest()
    original_count = manifest.page_count
    split_page(manifest, 6, [[0, 0, 100, 200], [100, 0, 200, 200]])

    xml_text = build_comicinfo_xml(manifest, META)
    assert validate_comicinfo_xml(xml_text, original_count + 1) == []

    output_path = tmp_path / "out.cbz"
    build_cbz(manifest, xml_text, output_path)
    try:
        with zipfile.ZipFile(output_path) as z:
            image_names = sorted(n for n in z.namelist() if n != "ComicInfo.xml")
            assert len(image_names) == original_count + 1
            piece_one = z.read(image_names[6])
            piece_two = z.read(image_names[7])
        assert get_image_size(piece_one) == (100, 200)
        assert get_image_size(piece_two) == (100, 200)
        # the two pieces are genuinely different crops of the same source,
        # not two copies of the same region
        assert piece_one != piece_two
    finally:
        output_path.unlink(missing_ok=True)
