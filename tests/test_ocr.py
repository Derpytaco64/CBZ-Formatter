from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from cbzforge import ocr

_FONT_CANDIDATES = [
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _load_font(size: int):
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Fall back to Pillow's bundled scalable default font - lower fidelity
    # for OCR than a real system font, but always available.
    return ImageFont.load_default(size=size)


def _render_text_image(lines: list[str]) -> bytes:
    font = _load_font(28)
    line_height = 80
    top_margin = 30
    img = Image.new("RGB", (900, line_height * len(lines) + top_margin * 2), "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((20, top_margin + i * line_height), line, fill="black", font=font)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_is_probably_credits_page_positive():
    text = "Translation: Jane Doe\nTypeset by Studio X\nAll rights reserved."
    assert ocr.is_probably_credits_page(text)


def test_is_probably_credits_page_negative():
    text = "Chihiro gripped the blade and stepped forward into the rain."
    assert not ocr.is_probably_credits_page(text)


def test_parse_toc_text_extracts_title_and_page():
    text = "Chapter 1: Mission..............5\nChapter 2: Heaps..............58\n"
    entries = ocr.parse_toc_text(text)
    assert entries == [
        ocr.TocEntry(title="Chapter 1: Mission", printed_page_number=5),
        ocr.TocEntry(title="Chapter 2: Heaps", printed_page_number=58),
    ]


def test_parse_toc_text_ignores_non_toc_lines():
    text = "just a normal sentence with no leaders\n"
    assert ocr.parse_toc_text(text) == []


def test_ocr_is_bundled_and_available():
    # No system package required - this must just work out of the box.
    assert ocr.is_available()


def test_extract_text_reads_a_rendered_toc_page():
    # OCR line-segmentation on a render can legitimately split one printed
    # line into two (a leader-dots run breaking off from its title) - that's
    # real OCR behavior, not a bug, and exactly why this layer is a
    # suggestion the GUI shows rather than something auto-committed. So this
    # checks the right content came through, not one exact line-split shape.
    image_bytes = _render_text_image(
        ["Chapter 1: Mission..........5", "Chapter 2: Heaps..........58"]
    )
    text = ocr.extract_text(image_bytes)
    lowered = text.lower()
    assert "chapter 1" in lowered
    assert "mission" in lowered
    assert "chapter 2" in lowered
    assert "heaps" in lowered


def test_extract_text_reads_a_rendered_credits_page():
    image_bytes = _render_text_image(["Translated by Jane Doe", "All rights reserved."])
    text = ocr.extract_text(image_bytes)
    assert ocr.is_probably_credits_page(text)
