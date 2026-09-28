"""Tests for the crop/split rectangle-selection tool on ImagePreviewLabel.
The riskiest part of this feature is coordinate mapping: mouse positions
are in the *displayed* (scaled-down, possibly letterboxed) pixmap's
coordinate space, but crop_box must end up in the *original* full-res
image's pixel coordinates - a bug here silently crops the wrong region.
"""
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QNativeGestureEvent, QPixmap, QPointingDevice
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGestureEvent, QInputDialog, QPinchGesture

from cbzforge.gui.widgets import ImagePreviewLabel


def _qapp():
    return QApplication.instance() or QApplication([])


def _drag(label, start: QPoint, end: QPoint) -> None:
    QTest.mousePress(label, Qt.LeftButton, pos=start)
    QTest.mouseMove(label, end)
    QTest.mouseRelease(label, Qt.LeftButton, pos=end)


def test_crop_drag_with_no_letterboxing_scales_to_original_size():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    # square source displayed in a square label -> no centering offset,
    # 10x scale factor between displayed (100x100) and original (1000x1000)
    label.set_source_pixmap(pixmap, original_size=(1000, 1000))
    label.set_selection_mode("crop")

    _drag(label, QPoint(10, 10), QPoint(30, 40))

    rects = label.get_source_rects()
    assert len(rects) == 1
    left, top, right, bottom = rects[0]
    assert abs(left - 100) <= 15
    assert abs(top - 100) <= 15
    assert abs(right - 310) <= 15
    assert abs(bottom - 410) <= 15
    assert 0 <= left < right <= 1000
    assert 0 <= top < bottom <= 1000


def test_crop_drag_accounts_for_letterboxing_offset():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(200, 200)
    # 2:1 aspect source in a square label -> KeepAspectRatio scales to
    # 200x100 and centers vertically, leaving a 50px gap top and bottom.
    pixmap = QPixmap(100, 50)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(1000, 500))
    label.set_selection_mode("crop")

    img_rect = label._displayed_image_rect()
    assert img_rect.height() == 100
    assert img_rect.top() > 40  # confirms there really is a vertical offset to get right

    # drag entirely within the displayed image band
    top_in_band = img_rect.top() + 10
    bottom_in_band = img_rect.top() + 60
    _drag(label, QPoint(20, top_in_band), QPoint(60, bottom_in_band))

    rects = label.get_source_rects()
    assert len(rects) == 1
    left, top, right, bottom = rects[0]
    # scale factor is 5x (1000/200); top should map relative to the 50px
    # offset, NOT the raw label y-coordinate - if the offset were ignored,
    # top would be way off (e.g. near (img_rect.top()+10)*5 = 300+ instead of ~50)
    assert 0 <= top <= 150
    assert 0 <= left < right <= 1000
    assert 0 <= top < bottom <= 500


def test_crop_mode_keeps_only_latest_rectangle():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")

    _drag(label, QPoint(5, 5), QPoint(20, 20))
    _drag(label, QPoint(50, 50), QPoint(70, 70))

    assert len(label.get_source_rects()) == 1


def test_split_mode_accumulates_multiple_rectangles():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("split")

    _drag(label, QPoint(5, 5), QPoint(20, 20))
    _drag(label, QPoint(50, 50), QPoint(70, 70))

    assert len(label.get_source_rects()) == 2


def test_clear_selection_resets_rectangles():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("split")
    _drag(label, QPoint(5, 5), QPoint(20, 20))
    assert label.has_selection()

    label.clear_selection()
    assert not label.has_selection()
    assert label.get_source_rects() == []


def test_tiny_drag_below_threshold_is_ignored():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")

    _drag(label, QPoint(10, 10), QPoint(11, 11))  # 1x1px - below the 5x5 threshold

    assert label.get_source_rects() == []


def test_no_selection_mode_does_not_start_a_drag():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    assert label.selection_mode == "none"

    _drag(label, QPoint(10, 10), QPoint(50, 50))

    assert label.get_source_rects() == []


# ---- editing an existing selection (move / resize) -------------------------


def test_dragging_rect_body_moves_it_without_creating_a_new_one():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")
    _drag(label, QPoint(10, 10), QPoint(40, 40))

    # (25, 25) is well inside the rectangle's body, away from every handle
    _drag(label, QPoint(25, 25), QPoint(35, 35))

    rects = label.get_source_rects()
    assert len(rects) == 1
    left, top, right, bottom = rects[0]
    assert abs(left - 20) <= 2
    assert abs(top - 20) <= 2


def test_dragging_a_handle_resizes_the_rect_instead_of_creating_a_new_one():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")
    _drag(label, QPoint(10, 10), QPoint(40, 40))

    # (40, 40) sits exactly on the bottom-right handle of the rect above
    _drag(label, QPoint(40, 40), QPoint(60, 70))

    rects = label.get_source_rects()
    assert len(rects) == 1
    left, top, right, bottom = rects[0]
    assert abs(left - 10) <= 2
    assert abs(top - 10) <= 2
    assert abs(right - 61) <= 3
    assert abs(bottom - 71) <= 3


def test_dragging_from_anywhere_along_an_edge_resizes_it_not_just_the_midpoint():
    """The 8 drawn handle squares (corners + edge midpoints) are a visual
    affordance, not the only grabbable spots - any point along an edge
    should start a single-edge resize, matching a standard cropper UI
    instead of forcing precise midpoint targeting."""
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")
    _drag(label, QPoint(10, 10), QPoint(60, 60))

    # (60, 20) is on the right edge, well away from its (60, 35) midpoint
    # handle and both corners - dragging it right should widen only the
    # right edge, leaving the left/top/bottom edges untouched.
    _drag(label, QPoint(60, 20), QPoint(80, 20))

    rects = label.get_source_rects()
    assert len(rects) == 1
    left, top, right, bottom = rects[0]
    assert abs(left - 10) <= 2
    assert abs(top - 10) <= 2
    assert abs(right - 81) <= 3
    assert abs(bottom - 61) <= 3


def test_set_committed_rects_from_source_prepopulates_selection():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")

    label.set_committed_rects_from_source([[20, 20, 60, 60]])

    assert label.has_selection()
    rects = label.get_source_rects()
    assert len(rects) == 1
    left, top, right, bottom = rects[0]
    assert abs(left - 20) <= 2
    assert abs(top - 20) <= 2
    assert abs(right - 60) <= 2
    assert abs(bottom - 60) <= 2


# ---- zoom --------------------------------------------------------------


def test_zoom_in_increases_effective_scale_and_label_size():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    assert label.zoom_percent() == 50  # fit-to-100x100 of a 200x200 source

    label.zoom_in()

    assert label.zoom_percent() > 50
    assert label.minimumSize() == label.maximumSize()  # fixed size while manually zoomed


def test_zoom_reset_returns_to_fit_and_unfixes_size():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    label.zoom_in()
    assert label.zoom_percent() != 50

    label.zoom_reset()

    assert label.zoom_percent() == 50
    assert label.minimumSize().width() == 1


def test_selection_is_retained_across_zoom_changes():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    label.set_selection_mode("crop")
    _drag(label, QPoint(10, 10), QPoint(40, 40))
    before = label.get_source_rects()[0]

    label.zoom_in()
    after_zoom_in = label.get_source_rects()[0]
    label.zoom_reset()
    after_reset = label.get_source_rects()[0]

    # Exact, not just approximate: get_source_rects() reads the canonical
    # source-space ground truth directly rather than re-deriving it from
    # the currently-displayed (rounded) rect, so it should never drift at
    # all regardless of how many times the view has been zoomed.
    assert after_zoom_in == before
    assert after_reset == before


def test_repeated_zoom_in_and_out_does_not_drift_selection_size():
    """A single zoom step's rounding error is invisible, but re-deriving
    _committed_rects from itself on every _rescale() (rather than from a
    stable ground truth) compounds that error every step - visibly
    shrinking or growing a selection over a long zoom in/out session even
    though the user never touched it. Regression test for that specific
    failure mode, distinct from single-step retention above."""
    _qapp()
    label = ImagePreviewLabel()
    label.resize(150, 150)
    pixmap = QPixmap(1000, 1400)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(1000, 1400))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(60, 100))
    before = label.get_source_rects()[0]

    for _ in range(10):
        label.zoom_in()
    for _ in range(10):
        label.zoom_out()

    assert label.get_source_rects()[0] == before


def test_selection_survives_a_real_window_resize_not_just_set_viewport_hint():
    """On a real window resize, Qt updates self.size() to the new value
    *before* resizeEvent (and thus _rescale) ever runs - unlike a
    synthetic set_viewport_hint() call, which is what the other retention
    tests use. _rescale() used to compute the "old" image rect live from
    self.width()/height() at that point, which is already the *new* size
    paired with the *old* (not-yet-replaced) pixmap - a mismatched pair
    that doesn't correspond to anything ever actually on screen. With a
    non-square source that produces letterboxing, that mismatch shifts the
    centering offset enough to silently drop every selection. A non-square
    source is required to expose it: a square source in a square label has
    a zero offset regardless of size, masking the bug entirely."""
    _qapp()
    label = ImagePreviewLabel()
    label.resize(150, 150)
    pixmap = QPixmap(300, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(300, 200))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 30), QPoint(60, 70))
    before = label.get_source_rects()
    assert before

    label.resize(150, 300)  # size changes even though resizeEvent may not fire in this headless setup
    label._rescale()  # what resizeEvent would trigger on a real, shown window
    after = label.get_source_rects()

    assert len(after) == 1
    assert abs(before[0][0] - after[0][0]) <= 2
    assert abs(before[0][1] - after[0][1]) <= 2
    assert abs(before[0][2] - after[0][2]) <= 2
    assert abs(before[0][3] - after[0][3]) <= 2


# ---- clamping to image bounds -----------------------------------------


def test_moving_a_selection_to_the_edge_does_not_shrink_it():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")
    _drag(label, QPoint(10, 10), QPoint(30, 30))
    before = label.get_source_rects()[0]
    before_w, before_h = before[2] - before[0], before[3] - before[1]

    # drag the rect's body far past the right/bottom edge of the image
    _drag(label, QPoint(20, 20), QPoint(150, 150))

    after = label.get_source_rects()[0]
    after_w, after_h = after[2] - after[0], after[3] - after[1]
    assert abs(after_w - before_w) <= 2
    assert abs(after_h - before_h) <= 2
    # it should have actually slid to the edge, not stayed put
    assert after[2] >= 95
    assert after[3] >= 95


def test_resizing_a_handle_past_the_edge_still_clamps_that_edge():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")
    _drag(label, QPoint(10, 10), QPoint(40, 40))

    # drag the bottom-right handle way past the image's edge
    _drag(label, QPoint(40, 40), QPoint(500, 500))

    rects = label.get_source_rects()
    assert len(rects) == 1
    left, top, right, bottom = rects[0]
    assert right <= 100
    assert bottom <= 100


# ---- border is drawn exclusive of the selected pixels ---------------------


def test_border_is_drawn_outside_the_selection_never_covering_a_selected_pixel():
    """The rendered border must never overlap the pixels it's marking as
    selected - everything strictly inside the rect is the pick, the border
    line itself sits just outside it. Uses two rects so the second (which
    gets no numbered-label badge check needed) stays unselected and thus
    draws no resize handles - handles are deliberately drawn on the true
    edge for grab precision and would otherwise be mistaken for a border
    overlap by this test."""
    _qapp()
    label = ImagePreviewLabel()
    label.resize(150, 150)
    pixmap = QPixmap(150, 150)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(150, 150))
    label.set_selection_mode("split")
    _drag(label, QPoint(20, 20), QPoint(60, 60))
    _drag(label, QPoint(70, 70), QPoint(130, 130))
    label.show()

    rect = label._committed_rects[0]  # first rect: unselected once the second exists, so no handles
    assert label._selected_rect_idx != 0

    image = QImage(label.size(), QImage.Format_ARGB32)
    label.render(image)

    def pixel(x, y):
        c = image.pixelColor(x, y)
        return (c.red(), c.green(), c.blue(), c.alpha())

    white = (255, 255, 255, 255)
    # The numbered-label badge fills the top-left 20x20 corner by design -
    # skip it, it's a separate pre-existing feature, not the border.
    badge_w, badge_h = 20, 20
    for x in range(rect.left(), rect.right() + 1):
        for y in range(rect.top(), rect.bottom() + 1):
            if x < rect.left() + badge_w and y < rect.top() + badge_h:
                continue
            assert pixel(x, y) == white, f"border/halo touched a selected pixel at ({x}, {y})"

    # And the border should be immediately outside (no gap) on all 4 sides.
    mid_x, mid_y = rect.left() + 30, rect.top() + 30
    assert pixel(rect.left() - 1, mid_y) != white
    assert pixel(rect.right() + 1, mid_y) != white
    assert pixel(mid_x, rect.top() - 1) != white
    assert pixel(mid_x, rect.bottom() + 1) != white


# ---- hover tooltip -------------------------------------------------------


def test_hover_over_a_selection_reports_its_index():
    app = _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    label.show()  # QTest.mouseMove's hover-only (no button) delivery needs a shown widget
    app.processEvents()
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")
    _drag(label, QPoint(10, 10), QPoint(40, 40))

    QTest.mouseMove(label, QPoint(25, 25))
    assert label.hover_index() == 0

    QTest.mouseMove(label, QPoint(90, 90))
    assert label.hover_index() is None


def test_hover_tooltip_size_does_not_wobble_across_zoom_changes(monkeypatch):
    """The tooltip must read the canonical source-space rect (see
    _committed_source_rects in widgets.py), not re-derive width/height from
    the current *display* rect - the display rect is only ever a rounded
    approximation, so re-converting it back would show a size that
    fluctuates by a pixel or two as the zoom level changes even though the
    actual stored/exported selection never moves."""
    from PySide6.QtWidgets import QToolTip

    _qapp()
    label = ImagePreviewLabel()
    label.resize(150, 150)
    pixmap = QPixmap(1000, 1400)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(1000, 1400))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(60, 100))

    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda pos, text, widget=None: shown.append(text))

    class FakeEvent:
        def globalPosition(self):
            class P:
                def toPoint(self):
                    return QPoint(0, 0)

            return P()

    label._set_hover_rect(0, FakeEvent())
    before = shown[-1]

    for _ in range(6):
        label.zoom_in()
        label._set_hover_rect(0, FakeEvent())

    assert shown[-1] == before


# ---- right-click context menu (delete / copy size) --------------------------


def test_right_click_delete_removes_the_selection(monkeypatch):
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("crop")
    _drag(label, QPoint(10, 10), QPoint(40, 40))
    assert label.has_selection()

    # "Delete selection" is the first action added in _show_context_menu.
    monkeypatch.setattr(
        ImagePreviewLabel, "_exec_context_menu", lambda self, menu, pos: menu.actions()[0]
    )
    QTest.mouseClick(label, Qt.RightButton, pos=QPoint(25, 25))

    assert not label.has_selection()


def test_right_click_copy_size_then_click_places_a_same_sized_selection(monkeypatch):
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("split")
    _drag(label, QPoint(5, 5), QPoint(25, 25))

    # "Copy size for new selection" is the second action added.
    monkeypatch.setattr(
        ImagePreviewLabel, "_exec_context_menu", lambda self, menu, pos: menu.actions()[1]
    )
    QTest.mouseClick(label, Qt.RightButton, pos=QPoint(15, 15))
    QTest.mouseClick(label, Qt.LeftButton, pos=QPoint(70, 70))

    rects = label.get_source_rects()
    assert len(rects) == 2
    w1, h1 = rects[0][2] - rects[0][0], rects[0][3] - rects[0][1]
    w2, h2 = rects[1][2] - rects[1][0], rects[1][3] - rects[1][1]
    # Exact, not approximate: "copy size" stores the exact source-pixel
    # size (see _pending_paste_size in widgets.py), so a paste must
    # reproduce it exactly rather than merely "close enough."
    assert (w1, h1) == (w2, h2)


def test_copy_size_survives_a_zoom_change_between_copy_and_paste(monkeypatch):
    """Regression test: "copy size" used to capture the *display* rect's
    size, which is scale-dependent - zooming between copying and pasting
    (or even pasting at a different position at the same zoom) would then
    silently paste a slightly different pixel size than what was actually
    copied. It must now store the exact source-pixel size and always
    reproduce it exactly, no matter what happens to the view in between."""
    _qapp()
    label = ImagePreviewLabel()
    label.resize(150, 150)
    pixmap = QPixmap(1000, 1400)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(1000, 1400))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(60, 100))
    original = label.get_source_rects()[0]
    original_size = (original[2] - original[0], original[3] - original[1])

    monkeypatch.setattr(
        ImagePreviewLabel, "_exec_context_menu", lambda self, menu, pos: menu.actions()[1]
    )
    QTest.mouseClick(label, Qt.RightButton, pos=QPoint(30, 50))

    label.zoom_in()
    label.zoom_in()
    label.zoom_in()

    QTest.mouseClick(label, Qt.LeftButton, pos=QPoint(100, 150))

    pasted = label.get_source_rects()[-1]
    pasted_size = (pasted[2] - pasted[0], pasted[3] - pasted[1])
    assert pasted_size == original_size


# ---- double-click a border to type in exact dimensions ---------------------


def test_double_click_on_border_prompts_for_and_applies_dimensions(monkeypatch):
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(40, 40))
    original_left, original_top = label.get_source_rects()[0][:2]

    # QInputDialog.getInt is a static method - monkeypatchable directly,
    # unlike QDialog.exec()/QMenu.exec() (see _exec_context_menu).
    calls = []

    def fake_get_int(parent, title, label_text, value, minValue, maxValue, *a, **k):
        calls.append((title, value, minValue, maxValue))
        return (150, True) if "width" in title.lower() else (90, True)

    monkeypatch.setattr(QInputDialog, "getInt", fake_get_int)

    # (10, 25) sits on the left border of the rect created above, well away
    # from its corners - a double-click anywhere along an edge (not just a
    # handle) must trigger the prompt, matching the resize-anywhere fix.
    QTest.mouseDClick(label, Qt.LeftButton, pos=QPoint(10, 25))

    assert len(calls) == 2  # prompted for width, then height
    rects = label.get_source_rects()
    assert len(rects) == 1
    left, top, right, bottom = rects[0]
    assert (right - left, bottom - top) == (150, 90)
    # top-left stays anchored where the selection already was
    assert (left, top) == (original_left, original_top)


def test_double_click_inside_a_selection_body_does_not_prompt(monkeypatch):
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(40, 40))

    calls = []
    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: calls.append(1) or (0, True))

    QTest.mouseDClick(label, Qt.LeftButton, pos=QPoint(25, 25))  # well inside, not on any border

    assert calls == []


def test_cancelling_the_width_prompt_leaves_the_selection_unchanged(monkeypatch):
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(40, 40))
    before = label.get_source_rects()

    monkeypatch.setattr(QInputDialog, "getInt", lambda *a, **k: (0, False))  # user hit Cancel

    QTest.mouseDClick(label, Qt.LeftButton, pos=QPoint(10, 25))

    assert label.get_source_rects() == before


# ---- two-finger pinch zoom ------------------------------------------------


def test_touch_pinch_gesture_zooms_in_and_out():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    before = label.zoom_percent()

    zoom_in_gesture = QPinchGesture()
    zoom_in_gesture.setScaleFactor(1.5)
    zoom_in_gesture.setChangeFlags(QPinchGesture.ChangeFlag.ScaleFactorChanged)
    label.event(QGestureEvent([zoom_in_gesture]))
    assert label.zoom_percent() > before

    after_zoom_in = label.zoom_percent()
    zoom_out_gesture = QPinchGesture()
    zoom_out_gesture.setScaleFactor(0.5)
    zoom_out_gesture.setChangeFlags(QPinchGesture.ChangeFlag.ScaleFactorChanged)
    label.event(QGestureEvent([zoom_out_gesture]))
    assert label.zoom_percent() < after_zoom_in


def test_pinch_gesture_without_scale_change_is_ignored():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    before = label.zoom_percent()

    gesture = QPinchGesture()  # no ScaleFactorChanged flag set
    label.event(QGestureEvent([gesture]))

    assert label.zoom_percent() == before


def test_trackpad_native_zoom_gesture_zooms_in_and_out():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(200, 200)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(200, 200))
    before = label.zoom_percent()
    device = QPointingDevice.primaryPointingDevice()

    zoom_in = QNativeGestureEvent(
        Qt.ZoomNativeGesture, device, 2, QPointF(0, 0), QPointF(0, 0), QPointF(0, 0), 0.2, QPointF(0, 0)
    )
    label.event(zoom_in)
    assert label.zoom_percent() > before

    after_zoom_in = label.zoom_percent()
    zoom_out = QNativeGestureEvent(
        Qt.ZoomNativeGesture, device, 2, QPointF(0, 0), QPointF(0, 0), QPointF(0, 0), -0.3, QPointF(0, 0)
    )
    label.event(zoom_out)
    assert label.zoom_percent() < after_zoom_in


# ---- selections can't overlap each other -----------------------------------


def test_creating_a_new_split_region_cannot_overlap_an_existing_one():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(40, 90))  # first region, right edge at x=40

    # start the drag clear of the first region (70,15 is away from any of
    # its resize handles) but drag back across it, overlapping its span
    _drag(label, QPoint(70, 15), QPoint(25, 90))

    rects = label.get_source_rects()
    assert len(rects) == 2
    first, second = rects
    # the new region must not intrude into the first one's span
    assert second[0] >= first[2] - 1


def test_adjacent_split_regions_share_a_boundary_with_no_pixel_gap_or_overlap():
    """crop_box is a half-open interval (right/bottom exclusive, matching
    PIL's crop() convention) - two regions clamped apart by the overlap
    prevention above must line up as an exact partition (region_a's right
    edge == region_b's left edge) so no source pixel column ends up
    excluded from every split piece, even under a non-integer zoom scale
    that could otherwise expose independent per-rect rounding drift."""
    _qapp()
    label = ImagePreviewLabel()
    label.resize(197, 197)  # awkward size vs. a 333px source: fractional scale
    pixmap = QPixmap(333, 333)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(333, 333))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(40, 90))
    _drag(label, QPoint(70, 15), QPoint(25, 90))  # forces the overlap-avoidance clamp

    first, second = label.get_source_rects()
    assert second[0] == first[2]


def test_moving_a_selection_into_a_neighbor_stops_at_its_edge_without_resizing():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(30, 30))  # region A
    _drag(label, QPoint(60, 10), QPoint(80, 30))  # region B, far to the right

    region_a_before = label.get_source_rects()[0]
    width_before = region_a_before[2] - region_a_before[0]
    height_before = region_a_before[3] - region_a_before[1]

    # select region A (click its body) and drag it toward/into region B
    label.selected_index()  # no-op read, just documents the getter exists
    QTest.mousePress(label, Qt.LeftButton, pos=QPoint(20, 20))
    QTest.mouseMove(label, QPoint(90, 20))
    QTest.mouseRelease(label, Qt.LeftButton, pos=QPoint(90, 20))

    rects = label.get_source_rects()
    region_a_after, region_b = rects[0], rects[1]
    width_after = region_a_after[2] - region_a_after[0]
    height_after = region_a_after[3] - region_a_after[1]
    assert abs(width_after - width_before) <= 2
    assert abs(height_after - height_before) <= 2
    # it should have actually slid up against region B, not stayed in place
    assert region_a_after[2] <= region_b[0] + 1


def test_resizing_a_selection_into_a_neighbor_stops_at_its_edge():
    _qapp()
    label = ImagePreviewLabel()
    label.resize(100, 100)
    pixmap = QPixmap(100, 100)
    pixmap.fill(Qt.white)
    label.set_source_pixmap(pixmap, original_size=(100, 100))
    label.set_selection_mode("split")
    _drag(label, QPoint(10, 10), QPoint(30, 30))  # region A
    _drag(label, QPoint(60, 10), QPoint(80, 30))  # region B

    region_b_before = label.get_source_rects()[1]

    # drag region A's right-edge handle far past region B
    _drag(label, QPoint(30, 20), QPoint(95, 20))

    rects = label.get_source_rects()
    region_a_after = rects[0]
    assert region_a_after[2] <= region_b_before[0] + 1
