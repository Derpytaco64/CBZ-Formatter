"""Reusable per-series metadata, so repeat volumes of the same series don't
need the same fields re-entered every time.

Also holds an optional chapter-number -> official-title lookup table,
populated once (pasted from MangaDex/a wiki/the metadata_sources lookups),
consulted by the GUI when suggesting bookmark titles for a new volume so
filename-vs-official discrepancies (see bookmarks.py / xmlgen.py tests) get
caught automatically instead of requiring a fresh lookup every volume.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SeriesProfile:
    name: str
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
    title_format: str = "{series} Vol. {volume}"
    volume_zero_pad: int = 0
    series_synopsis: str = ""
    chapter_titles: dict[str, str] = field(default_factory=dict)

    def format_title(self, volume: int) -> str:
        vol_str = str(volume).zfill(self.volume_zero_pad) if self.volume_zero_pad else str(volume)
        return self.title_format.format(series=self.name, volume=vol_str)

    def official_title_for(self, chapter_number: str) -> str | None:
        return self.chapter_titles.get(chapter_number.lstrip("0") or "0")

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SeriesProfile":
        return cls(**data)

    def save(self, path: str | Path) -> None:
        import yaml

        Path(path).write_text(yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True))

    @classmethod
    def load(cls, path: str | Path) -> "SeriesProfile":
        import yaml

        data = yaml.safe_load(Path(path).read_text())
        return cls.from_dict(data)


def _safe_filename(series_name: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in series_name)
    return safe.strip().strip("_") or "series"


def profile_path(profiles_dir: str | Path, series_name: str) -> Path:
    return Path(profiles_dir) / f"{_safe_filename(series_name)}.yaml"


def load_or_create(profiles_dir: str | Path, series_name: str) -> SeriesProfile:
    path = profile_path(profiles_dir, series_name)
    if path.exists():
        return SeriesProfile.load(path)
    return SeriesProfile(name=series_name)
