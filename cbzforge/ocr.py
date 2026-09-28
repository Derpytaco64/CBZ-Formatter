"""OCR-based suggestions for plain-print pages (table of contents, copyright /
credits pages). Never trusted blindly - every function here returns a
suggestion for the GUI to show, not a decision.

Uses RapidOCR (onnxruntime), a pure pip-installable engine with its models
bundled in the wheel - no external binary or system package (unlike
Tesseract), so OCR keeps working in a portable/frozen bundle with nothing
extra to install on the target machine.

Stylized chapter-title splash pages are deliberately NOT handled here;
OCR (any engine) is unreliable on decorative manga typography integrated
into artwork, so that classification belongs to a vision-model pass or
manual GUI entry instead - see the project outline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

CREDITS_KEYWORDS = (
    "scanlation",
    "translated by",
    "typeset",
    "redrawn",
    "proofread",
    "all rights reserved",
    "no part of this book",
    "printed in",
)

# "Chapter Title .......... 123" style table-of-contents line
TOC_LINE_RE = re.compile(r"(?P<title>.+?)\.{2,}\s*(?P<page>\d{1,4})\s*$")


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR

    return RapidOCR()


def is_available() -> bool:
    try:
        _engine()
    except Exception:
        return False
    return True


def extract_text(image_bytes: bytes) -> str:
    try:
        engine = _engine()
    except Exception as exc:  # pragma: no cover - only hit if the bundled dep is broken
        raise RuntimeError(f"OCR engine failed to load: {exc}") from exc

    result, _elapse = engine(image_bytes)
    if not result:
        return ""
    return "\n".join(text for _box, text, _score in result)


def is_probably_credits_page(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in CREDITS_KEYWORDS)


@dataclass
class TocEntry:
    title: str
    printed_page_number: int


def parse_toc_text(text: str) -> list[TocEntry]:
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = TOC_LINE_RE.match(line)
        if m:
            entries.append(
                TocEntry(
                    title=m.group("title").strip(" .-"),
                    printed_page_number=int(m.group("page")),
                )
            )
    return entries
