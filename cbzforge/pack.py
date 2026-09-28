"""Phase 2 (part 2): rename pages to a sequential order and package the final CBZ.

Reads every image's bytes in confirmed manifest order BEFORE writing anything,
then verifies the built archive byte-for-byte against what was read - this is
the same safeguard the original manual workflow used to catch accidental
reordering introduced by the rename step.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from cbzforge.imaging import apply_crop
from cbzforge.manifest import Manifest, Page
from cbzforge.page_ops import resolve_output_pages


def _zero_pad_width(count: int) -> int:
    return max(3, len(str(max(count - 1, 0))))


def _read_source_bytes(manifest: Manifest, pages: list[Page]) -> list[bytes]:
    source = Path(manifest.source)
    is_zip = source.is_file() and source.suffix.lower() in {".cbz", ".zip"}

    if is_zip:
        with zipfile.ZipFile(source) as z:
            raw = [z.read(page.source_name) for page in pages]
    else:
        raw = [(source / page.source_name).read_bytes() for page in pages]

    return [
        apply_crop(data, page.crop_box) if page.crop_box else data
        for page, data in zip(pages, raw)
    ]


def build_cbz(manifest: Manifest, comicinfo_xml: str, output_path: str | Path) -> Path:
    output_pages = resolve_output_pages(manifest)
    if not output_pages:
        raise ValueError("every page is excluded - nothing left to build")

    page_bytes = _read_source_bytes(manifest, output_pages)
    width = _zero_pad_width(len(page_bytes))

    entries: list[tuple[str, bytes]] = []
    for output_position, (page, data) in enumerate(zip(output_pages, page_bytes)):
        ext = Path(page.source_name).suffix.lower()
        arcname = f"{output_position:0{width}d}{ext}"
        entries.append((arcname, data))

    output_path = Path(output_path)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("ComicInfo.xml", comicinfo_xml)
            for arcname, data in entries:
                z.writestr(arcname, data)

        _verify_cbz(tmp_path, entries, comicinfo_xml)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    tmp_path.replace(output_path)
    return output_path


def _verify_cbz(path: Path, entries: list[tuple[str, bytes]], comicinfo_xml: str) -> None:
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad is not None:
            raise IOError(f"corrupt entry in freshly-built archive: {bad}")

        names = set(z.namelist())
        if "ComicInfo.xml" not in names:
            raise IOError("ComicInfo.xml missing from built archive")
        if z.read("ComicInfo.xml").decode("utf-8") != comicinfo_xml:
            raise IOError("ComicInfo.xml content mismatch after packaging")

        expected_names = {arcname for arcname, _ in entries} | {"ComicInfo.xml"}
        if names != expected_names:
            raise IOError("built archive entry set does not match expected pages")

        for arcname, data in entries:
            if z.read(arcname) != data:
                raise IOError(f"byte mismatch for {arcname} after packaging")
