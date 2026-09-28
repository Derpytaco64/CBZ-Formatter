"""Read-only lookups against external metadata sources.

Every function here returns plain data for the GUI to show as an editable
suggestion - nothing in this module writes to a Manifest or SeriesProfile
directly, and nothing here is trusted as final. See the project outline:
MangaDex is the best structured source for per-chapter titles keyed to
volume/chapter number; AniList covers series-level metadata (staff, genres,
synopsis, publication info) that MangaDex doesn't reliably carry.
"""
from __future__ import annotations

import requests

MANGADEX_API = "https://api.mangadex.org"
ANILIST_API = "https://graphql.anilist.co"
DEFAULT_TIMEOUT = 15


def mangadex_search_manga(title: str, limit: int = 5) -> list[dict]:
    """Search MangaDex by title. Returns [{id, title, year}, ...]."""
    resp = requests.get(
        f"{MANGADEX_API}/manga",
        params={"title": title, "limit": limit},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    results = []
    for item in resp.json().get("data", []):
        attrs = item["attributes"]
        titles = attrs.get("title", {})
        results.append(
            {
                "id": item["id"],
                "title": titles.get("en") or next(iter(titles.values()), ""),
                "year": attrs.get("year"),
            }
        )
    return results


def mangadex_chapter_titles(manga_id: str, language: str = "en") -> list[dict]:
    """Return every chapter for a manga as {volume, chapter, title}, in
    reading order, paginating through MangaDex's 100-item page limit.
    """
    chapters: list[dict] = []
    offset = 0
    limit = 100
    while True:
        resp = requests.get(
            f"{MANGADEX_API}/chapter",
            params={
                "manga": manga_id,
                "translatedLanguage[]": language,
                "order[volume]": "asc",
                "order[chapter]": "asc",
                "limit": limit,
                "offset": offset,
            },
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        for item in payload.get("data", []):
            attrs = item["attributes"]
            chapters.append(
                {
                    "volume": attrs.get("volume"),
                    "chapter": attrs.get("chapter"),
                    "title": attrs.get("title") or "",
                }
            )
        total = payload.get("total", 0)
        offset += limit
        if offset >= total:
            break
    return chapters


ANILIST_QUERY = """
query ($search: String) {
  Media(search: $search, type: MANGA) {
    title { romaji english }
    description(asHtml: false)
    genres
    staff(perPage: 10) {
      edges { role node { name { full } } }
    }
    startDate { year month day }
  }
}
"""


def anilist_series_metadata(title: str) -> dict | None:
    """Series-level metadata: title, description, genres, staff, start date."""
    resp = requests.post(
        ANILIST_API,
        json={"query": ANILIST_QUERY, "variables": {"search": title}},
        timeout=DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    media = resp.json().get("data", {}).get("Media")
    if not media:
        return None

    staff: dict[str, list[str]] = {}
    for edge in media.get("staff", {}).get("edges", []):
        role = edge.get("role", "")
        name = edge.get("node", {}).get("name", {}).get("full", "")
        staff.setdefault(role, []).append(name)

    return {
        "title": media["title"].get("english") or media["title"].get("romaji"),
        "description": media.get("description"),
        "genres": media.get("genres", []),
        "staff": staff,
        "start_date": media.get("startDate"),
    }
