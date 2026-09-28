from cbzforge.profiles import SeriesProfile, load_or_create, profile_path


def test_format_title_with_and_without_padding():
    p = SeriesProfile(name="Kagurabachi")
    assert p.format_title(1) == "Kagurabachi Vol. 1"

    p.volume_zero_pad = 2
    assert p.format_title(1) == "Kagurabachi Vol. 01"


def test_official_title_lookup_strips_leading_zeros():
    p = SeriesProfile(name="Kagurabachi", chapter_titles={"8": "Norisaku Madoka—I Will Change"})
    assert p.official_title_for("008") == "Norisaku Madoka—I Will Change"
    assert p.official_title_for("999") is None


def test_save_load_roundtrip(tmp_path):
    p = SeriesProfile(
        name="Kagurabachi",
        writer="Takeru Hokazono",
        publisher="VIZ Media",
        imprint="SHONEN JUMP",
        chapter_titles={"1": "Mission"},
    )
    path = profile_path(tmp_path, p.name)
    p.save(path)

    loaded = SeriesProfile.load(path)
    assert loaded == p


def test_load_or_create_makes_default_when_missing(tmp_path):
    p = load_or_create(tmp_path, "New Series")
    assert p.name == "New Series"
    assert p.writer == ""
