from pathlib import Path

from cbzforge.scan import build_manifest

FIXTURE = (
    Path(__file__).parent.parent
    / "Kagurabachi Vol. 1 - Takeru Hokazono.cbz"
)


def test_build_manifest_kagurabachi():
    manifest = build_manifest(FIXTURE)

    assert manifest.parser_used == "flat_digital"
    assert manifest.page_count == 202
    assert manifest.corruption is not None
    assert manifest.corruption.clean
    assert manifest.corruption.bad_entries == []

    # 8 real chapters + the c008x1 bonus comic block = 9 distinct
    # chapter_number groups in reading order.
    numbers = [b.chapter_number for b in manifest.chapter_boundaries]
    assert numbers == ["001", "002", "003", "004", "005", "006", "007", "008", "008x1"]

    titles = [b.chapter_title for b in manifest.chapter_boundaries]
    assert titles == [
        "Mission",
        "Heaps",
        "Witness",
        "Sorcery and the Enchanted Blade",
        "A Good Meal",
        "Peace",
        "Smoke Signal",
        # filename uses a plain hyphen; the verified ComicInfo.xml for this
        # volume uses an em dash here ("Norisaku Madoka—I Will Change") -
        # exactly the kind of filename-vs-official discrepancy the series
        # profile / metadata lookup step is meant to catch and correct.
        "Norisaku Madoka - I Will Change",
        "Bonus Comic",
    ]

    # first page carries the [Cover] special tag
    assert manifest.pages[0].special_tag == "Cover"
    assert manifest.front_cover_index == 0

    # page order must be gapless and match page_count
    assert [p.index for p in manifest.pages] == list(range(202))
