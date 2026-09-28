import zipfile
from pathlib import Path

import pytest

from cbzforge.bookmarks import suggest_bookmarks
from cbzforge.scan import build_manifest
from cbzforge.xmlgen import SeriesMetadata, build_comicinfo_xml, validate_comicinfo_xml
from cbzforge.pack import build_cbz

FIXTURE = (
    Path(__file__).parent.parent
    / "Kagurabachi Vol. 1 - Takeru Hokazono.cbz"
)


def test_suggest_bookmarks_naive_guess_collides_with_cover():
    """scan.py can't know where chapter 1's real story content starts vs.
    front matter (cover/ToC) that shares its chapter-number tag - Step 3 of
    the original workflow doc calls this out explicitly. The naive
    suggestion should collide, and build_comicinfo_xml must refuse to
    silently pick one over the other; this is exactly the case the GUI's
    manual placement step exists to resolve.
    """
    manifest = build_manifest(FIXTURE)
    manifest.bookmarks = suggest_bookmarks(manifest)

    page_zero_bookmarks = [b for b in manifest.bookmarks if b.page_index == 0]
    assert {b.type for b in page_zero_bookmarks} == {"FrontCover", "Story"}

    meta = SeriesMetadata(series="Kagurabachi", volume=1, number="1.0", title="Kagurabachi Vol. 1")
    with pytest.raises(ValueError, match="more than one bookmark"):
        build_comicinfo_xml(manifest, meta)


def test_full_pipeline_end_to_end(tmp_path):
    manifest = build_manifest(FIXTURE)
    manifest.bookmarks = suggest_bookmarks(manifest)

    # Simulate the GUI correction: the user drags the "Chapter 1" bookmark
    # off the cover page and onto page 5, where the real story content
    # starts (matches the verified reference ComicInfo.xml for this volume).
    chapter_one = next(
        b for b in manifest.bookmarks if b.type == "Story" and b.label.startswith("Chapter 1:")
    )
    chapter_one.page_index = 5

    labels = {b.page_index: (b.type, b.label) for b in manifest.bookmarks}
    assert labels[0] == ("FrontCover", "Cover")
    assert labels[5] == ("Story", "Chapter 1: Mission")
    assert not any("8.1" in b.label for b in manifest.bookmarks)
    assert ("Other", "Bonus Content") in labels.values()

    meta = SeriesMetadata(
        series="Kagurabachi",
        volume=1,
        number="1.0",
        title="Kagurabachi Vol. 1",
        writer="Takeru Hokazono",
        publisher="VIZ Media",
        imprint="SHONEN JUMP",
    )

    xml_text = build_comicinfo_xml(manifest, meta)
    errors = validate_comicinfo_xml(xml_text, manifest.page_count)
    assert errors == []

    output_path = tmp_path / "Kagurabachi Vol. 1 - rebuilt.cbz"
    build_cbz(manifest, xml_text, output_path)

    try:
        assert output_path.exists()
        with zipfile.ZipFile(output_path) as z:
            assert z.testzip() is None
            names = z.namelist()
            assert "ComicInfo.xml" in names
            image_names = sorted(n for n in names if n != "ComicInfo.xml")
            assert len(image_names) == manifest.page_count
            # sequential zero-padded rename, gapless
            assert image_names[0] == "000.jpg"
            assert image_names[-1] == f"{manifest.page_count - 1:03d}.jpg"
    finally:
        # tmp_path lives on a small tmpfs in this environment and pytest
        # keeps several past runs around - don't let a ~225MB rebuilt CBZ
        # linger after the test, or it fills the disk over a few runs.
        output_path.unlink(missing_ok=True)
