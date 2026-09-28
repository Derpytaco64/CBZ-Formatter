"""Custom widgets for the main window:
- FlowLayout: wraps button rows onto multiple lines instead of clipping
  them when the right panel is narrow.
- CollapsibleSection: click-to-toggle section header (see its own docstring).
- ImagePreviewLabel: the large page viewer, doubling as the crop/split
  rectangle-selection tool.
- BookmarkListWidget / PageThumbnailGrid: the drag-and-drop pair for TOC
  placement (bookmark -> page) and page reordering (page -> page), kept
  dumb (no Manifest mutation) - they only report indices via signals;
  MainWindow owns the actual data mutation so it stays easy to test without
  simulating real Qt drag gestures.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QInputDialog,
    QLabel,
    QLayout,
    QListWidget,
    QMenu,
    QPinchGesture,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

BOOKMARK_MIME = "application/x-cbzforge-bookmark-index"
PAGE_MIME = "application/x-cbzforge-page-index"

SELECTION_COLORS = [
    QColor(255, 196, 0),
    QColor(0, 200, 120),
    QColor(80, 170, 255),
    QColor(255, 100, 100),
    QColor(200, 120, 255),
]


class FlowLayout(QLayout):
    """Standard Qt "flow layout" recipe: lays children left-to-right,
    wrapping to a new line when the row runs out of width. Used for button
    rows in the right panel so they stay reachable at any panel width
    instead of getting clipped by a fixed QHBoxLayout."""

    def __init__(self, parent=None, margin: int = 0, spacing: int = 6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items: list = []

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only: bool) -> int:
        x, y = rect.x(), rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y += line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))

            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y()


HANDLE_NAMES = ("tl", "tr", "bl", "br", "t", "b", "l", "r")

CURSOR_FOR_HANDLE = {
    "tl": Qt.SizeFDiagCursor,
    "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor,
    "bl": Qt.SizeBDiagCursor,
    "t": Qt.SizeVerCursor,
    "b": Qt.SizeVerCursor,
    "l": Qt.SizeHorCursor,
    "r": Qt.SizeHorCursor,
}


class ImagePreviewLabel(QLabel):
    """Shows a page, either scaled to fit the pane or at a manual zoom
    level (in which case a surrounding QScrollArea supplies scrollbars for
    panning). Also doubles as the crop/split selection tool: when
    selection_mode is "crop" or "split", dragging on empty space defines a
    new rectangle (crop mode keeps only one; split mode accumulates
    several, numbered). Once a rectangle exists it becomes directly
    editable like a normal image cropper: drag its body to move it, drag
    one of its 8 handles to resize it, right-click it to delete it or copy
    its size for stamping a same-sized rectangle elsewhere. Rectangles are
    tracked in this widget's own display coordinates and converted to the
    true source image's pixel coordinates on demand via get_source_rects(),
    since the displayed pixmap is scaled from the original.
    """

    zoomChanged = Signal(float)

    ZOOM_STEP = 1.25
    MIN_ZOOM = 0.1
    MAX_ZOOM = 8.0
    HANDLE_TOLERANCE = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_pixmap: QPixmap | None = None
        self._original_size: tuple[int, int] | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setText("Select a page to preview it here")
        self.setMouseTracking(True)
        # Touchscreen two-finger pinch is delivered as a QGestureEvent, but
        # only once something has grabbed the gesture; trackpad two-finger
        # pinch is delivered separately as a QNativeGestureEvent and needs
        # no grab - both are handled together in event() below.
        self.grabGesture(Qt.PinchGesture)

        self.selection_mode: str = "none"  # "none" | "crop" | "split"
        self._committed_rects: list[QRect] = []
        # Canonical ground truth for _committed_rects, in original-image
        # pixel coordinates - kept in lockstep with _committed_rects by every
        # direct edit (see _remember_source_rect) and never itself derived
        # from a previously-rounded display value. _rescale() always
        # regenerates _committed_rects fresh from this list rather than by
        # converting the current (possibly already-rounded) _committed_rects
        # through source space and back - re-deriving from an already-rounded
        # value on every zoom step compounds truncation error, so repeated
        # zoom in/out would otherwise visibly shrink or grow selections over
        # time even though nothing about them was ever edited.
        self._committed_source_rects: list[list[int]] = []
        self._selected_rect_idx: int | None = None
        self._hover_idx: int | None = None
        # Source-image pixel dimensions (width, height) from "copy size" -
        # deliberately *not* a display QSize, see _place_pasted_rect.
        self._pending_paste_size: tuple[int, int] | None = None

        self._drag_mode: str | None = None  # None | "create" | "move" | "resize:<handle>"
        self._drag_start: QPoint | None = None
        self._drag_rect: QRect | None = None
        self._drag_orig_rect: QRect | None = None

        self._zoom_mode: str = "fit"  # "fit" | "manual"
        self._zoom_factor: float = 1.0
        # The size to fit against - tracked separately from self.size()
        # because while zoomed in, this widget's own size is pinned to the
        # (larger) zoomed pixmap size, not the space actually available; if
        # "fit" scale were computed from self.size() at that point, resetting
        # zoom right after zooming in would compute fit against the
        # zoomed-in size instead of the real viewport, and never shrink back
        # down. PreviewScrollArea below keeps this current with the real
        # viewport size; resizeEvent is a fallback for a label used standalone.
        self._fit_size_hint = QSize()

    # ---- content -------------------------------------------------------

    def set_source_pixmap(self, pixmap: QPixmap, original_size: tuple[int, int] | None = None) -> None:
        self._source_pixmap = pixmap
        self._original_size = original_size or (pixmap.width(), pixmap.height())
        self.clear_selection()
        self._rescale()

    def clear_preview(self, placeholder: str = "Select a page to preview it here") -> None:
        self._source_pixmap = None
        self._original_size = None
        self.clear_selection()
        self._zoom_mode = "fit"
        self.setMinimumSize(1, 1)
        self.setMaximumSize(16777215, 16777215)
        self.setPixmap(QPixmap())
        self.setText(placeholder)

    def set_viewport_hint(self, size: QSize) -> None:
        """Told by PreviewScrollArea whenever the real viewport resizes -
        the authoritative source for "fit" scale, since it stays accurate
        even while this widget's own size is currently pinned by zoom."""
        self._fit_size_hint = size
        if self._zoom_mode == "fit":
            self._rescale()

    def resizeEvent(self, event) -> None:
        if self._zoom_mode == "fit":
            self._fit_size_hint = self.size()
            self._rescale()
        super().resizeEvent(event)

    def _effective_scale(self) -> float:
        if not self._original_size or self._original_size[0] <= 0 or self._original_size[1] <= 0:
            return 1.0
        if self._zoom_mode == "manual":
            return self._zoom_factor
        # QSize() defaults to (-1, -1) (Qt's "invalid size" sentinel), not
        # (0, 0), so this can't be a plain `x or y` falsy-fallback check.
        if self._fit_size_hint.isValid() and self._fit_size_hint.width() > 0:
            width, height = self._fit_size_hint.width(), self._fit_size_hint.height()
        else:
            width, height = self.width(), self.height()
        if width <= 0 or height <= 0:
            return 1.0
        return min(width / self._original_size[0], height / self._original_size[1])

    def _rescale(self) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        # Opportunistically capture a valid fit hint if none exists yet,
        # rather than relying solely on resizeEvent/PreviewScrollArea having
        # already fired - Qt doesn't guarantee resizeEvent delivery is
        # synchronous with resize() for a widget that isn't shown yet, and
        # this is the only other place that's safe to grab it (still "fit").
        if self._zoom_mode == "fit" and not (self._fit_size_hint.isValid() and self._fit_size_hint.width() > 0):
            self._fit_size_hint = self.size()

        scale = self._effective_scale()
        target = QSize(
            max(1, round(self._original_size[0] * scale)),
            max(1, round(self._original_size[1] * scale)),
        )
        scaled = self._source_pixmap.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)
        if self._zoom_mode == "manual":
            self.setMinimumSize(scaled.size())
            self.setMaximumSize(scaled.size())
        else:
            self.setMinimumSize(1, 1)
            self.setMaximumSize(16777215, 16777215)

        # Always regenerate _committed_rects fresh from the canonical
        # source-space ground truth rather than re-deriving it from the
        # previous (already-rounded) _committed_rects - see
        # _committed_source_rects' definition in __init__ for why.
        if self._committed_source_rects:
            rects, sources = [], []
            for src in self._committed_source_rects:
                display = self._source_rect_to_display(src)
                if display is not None:
                    rects.append(display)
                    sources.append(src)
            self._committed_rects = rects
            self._committed_source_rects = sources
            if self._selected_rect_idx is not None and self._selected_rect_idx >= len(self._committed_rects):
                self._selected_rect_idx = None

    # ---- zoom --------------------------------------------------------

    def zoom_in(self) -> None:
        self._set_manual_zoom(self._effective_scale() * self.ZOOM_STEP)

    def zoom_out(self) -> None:
        self._set_manual_zoom(self._effective_scale() / self.ZOOM_STEP)

    def zoom_reset(self) -> None:
        self._zoom_mode = "fit"
        self._rescale()
        self.zoomChanged.emit(self._effective_scale())

    def zoom_percent(self) -> int:
        return round(self._effective_scale() * 100)

    def _set_manual_zoom(self, scale: float) -> None:
        if self._source_pixmap is None:
            return
        self._zoom_mode = "manual"
        self._zoom_factor = max(self.MIN_ZOOM, min(scale, self.MAX_ZOOM))
        self._rescale()
        self.zoomChanged.emit(self._effective_scale())

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier and self._source_pixmap is not None:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def _apply_relative_zoom(self, factor: float) -> None:
        """Multiplies the current scale by `factor` (as opposed to
        zoom_in/zoom_out's fixed step) - what a continuous two-finger pinch
        gesture reports incrementally as it moves."""
        if self._source_pixmap is None or factor <= 0:
            return
        self._set_manual_zoom(self._effective_scale() * factor)

    def event(self, event) -> bool:
        if event.type() == QEvent.Gesture:
            pinch = event.gesture(Qt.PinchGesture)
            if pinch is not None:
                if pinch.changeFlags() & QPinchGesture.ChangeFlag.ScaleFactorChanged:
                    self._apply_relative_zoom(pinch.scaleFactor())
                return True
        elif event.type() == QEvent.NativeGesture:
            if event.gestureType() == Qt.ZoomNativeGesture:
                # value() is the incremental change to add to the running
                # scale factor, per QNativeGestureEvent's docs.
                self._apply_relative_zoom(1.0 + event.value())
                return True
        return super().event(event)

    # ---- selection tool --------------------------------------------------

    def set_selection_mode(self, mode: str) -> None:
        assert mode in ("none", "crop", "split")
        self.selection_mode = mode
        self.clear_selection()

    def clear_selection(self) -> None:
        self._committed_rects = []
        self._committed_source_rects = []
        self._selected_rect_idx = None
        self._hover_idx = None
        self._pending_paste_size = None
        self._drag_mode = None
        self._drag_start = None
        self._drag_rect = None
        self._drag_orig_rect = None
        self.unsetCursor()
        QToolTip.hideText()
        self.update()

    def has_selection(self) -> bool:
        return bool(self._committed_rects)

    def hover_index(self) -> int | None:
        return self._hover_idx

    def selected_index(self) -> int | None:
        return self._selected_rect_idx

    def get_source_rects(self) -> list[list[int]]:
        # _committed_source_rects is the canonical ground truth (see its
        # definition in __init__) - returning it directly, rather than
        # re-deriving from the current (rounded) _committed_rects, is what
        # keeps the actual crop/split output exact regardless of how many
        # times the view has been zoomed or resized since the edit was made.
        return [list(r) for r in self._committed_source_rects]

    def set_committed_rects_from_source(self, source_rects: list[list[int]]) -> None:
        """Pre-populates the selection from existing source-space rect(s),
        e.g. showing a page's current crop_box as an adjustable box when
        crop mode is entered again, instead of starting from nothing."""
        rects, sources = [], []
        for sr in source_rects:
            display = self._source_rect_to_display(sr)
            if display is not None:
                rects.append(display)
                sources.append(list(sr))
        self._committed_rects = rects
        self._committed_source_rects = sources
        self._selected_rect_idx = 0 if rects else None
        self.update()

    def _displayed_image_rect(self) -> QRect:
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRect()
        x = (self.width() - pixmap.width()) // 2
        y = (self.height() - pixmap.height()) // 2
        return QRect(x, y, pixmap.width(), pixmap.height())

    def _display_rect_to_source(self, rect: QRect, img_rect: QRect) -> list[int] | None:
        clipped = rect.intersected(img_rect)
        if clipped.isEmpty() or self._original_size is None:
            return None
        scale_x = self._original_size[0] / img_rect.width()
        scale_y = self._original_size[1] / img_rect.height()
        left = int((clipped.left() - img_rect.left()) * scale_x)
        top = int((clipped.top() - img_rect.top()) * scale_y)
        right = int((clipped.right() - img_rect.left() + 1) * scale_x)
        bottom = int((clipped.bottom() - img_rect.top() + 1) * scale_y)
        left = max(0, min(left, self._original_size[0]))
        top = max(0, min(top, self._original_size[1]))
        right = max(0, min(right, self._original_size[0]))
        bottom = max(0, min(bottom, self._original_size[1]))
        if right - left < 1 or bottom - top < 1:
            return None
        return [left, top, right, bottom]

    def _source_rect_to_display(self, source_rect: list[int]) -> QRect | None:
        img_rect = self._displayed_image_rect()
        if img_rect.isEmpty() or self._original_size is None:
            return None
        scale_x = img_rect.width() / self._original_size[0]
        scale_y = img_rect.height() / self._original_size[1]
        left, top, right, bottom = source_rect
        x1 = img_rect.left() + left * scale_x
        y1 = img_rect.top() + top * scale_y
        x2 = img_rect.left() + right * scale_x
        y2 = img_rect.top() + bottom * scale_y
        rect = QRect(QPoint(round(x1), round(y1)), QPoint(round(x2) - 1, round(y2) - 1)).normalized()
        return rect if rect.width() >= 1 and rect.height() >= 1 else None

    def _clamp_edges(self, rect: QRect, exclude_idx: int | None = None) -> QRect:
        """Clamps each edge independently to the image bounds - right for
        creating a new selection or resizing via a handle, where the edge
        being dragged should simply stop at the boundary. Other committed
        selections (in split mode) are treated the same way, as additional
        boundaries a selection can't be drawn/resized across - see
        _shrink_to_avoid_overlap for the "which edge moved" heuristic."""
        img_rect = self._displayed_image_rect()
        if not img_rect.isEmpty():
            left = max(img_rect.left(), min(rect.left(), img_rect.right()))
            top = max(img_rect.top(), min(rect.top(), img_rect.bottom()))
            right = max(img_rect.left(), min(rect.right(), img_rect.right()))
            bottom = max(img_rect.top(), min(rect.bottom(), img_rect.bottom()))
            if right - left < 5:
                right = min(img_rect.right(), left + 5)
            if bottom - top < 5:
                bottom = min(img_rect.bottom(), top + 5)
            rect = QRect(QPoint(left, top), QPoint(right, bottom)).normalized()

        for i, other in enumerate(self._committed_rects):
            if i != exclude_idx:
                rect = self._shrink_to_avoid_overlap(rect, other)
        return rect

    def _shrink_to_avoid_overlap(self, rect: QRect, other: QRect) -> QRect:
        """Pulls back whichever edge of `rect` is overlapping `other`, using
        the axis with the smaller overlap as a heuristic for which edge is
        actually the one that moved into it - correct for the common case
        of adjacent strips (a resize/create drag approaching a neighbor
        mostly overlaps along one axis at a time), though not a general
        multi-rectangle constraint solver."""
        overlap = rect.intersected(other)
        if overlap.isEmpty():
            return rect
        left, top, right, bottom = rect.left(), rect.top(), rect.right(), rect.bottom()
        if overlap.width() <= overlap.height():
            if rect.center().x() < other.center().x():
                right = min(right, other.left() - 1)
            else:
                left = max(left, other.right() + 1)
        else:
            if rect.center().y() < other.center().y():
                bottom = min(bottom, other.top() - 1)
            else:
                top = max(top, other.bottom() + 1)
        if right <= left:
            right = left + 1
        if bottom <= top:
            bottom = top + 1
        return QRect(QPoint(left, top), QPoint(right, bottom)).normalized()

    def _clamp_move(self, rect: QRect, exclude_idx: int | None = None, reference: QRect | None = None) -> QRect:
        """Keeps a moved selection inside the image bounds (and outside
        every other committed selection) by sliding it back, preserving its
        size - clamping each edge independently (like _clamp_edges) would
        shrink the rect the instant it touches a boundary, which reads as
        an unwanted resize rather than the drag just stopping.

        `reference` should be the rect's pre-drag position, used only to
        decide which side of an overlapping neighbor to push back towards -
        using the (possibly deep-into-the-neighbor) candidate `rect` itself
        for that decision breaks on a fast/large drag that ends up mostly
        past the neighbor, since by then it reads as "approaching from the
        other side" and gets pushed the wrong way. Defaults to `rect`
        itself when there's no meaningful "before" position (e.g. pasting
        a copied-size selection fresh)."""
        img_rect = self._displayed_image_rect()
        if not img_rect.isEmpty():
            dx = 0
            if rect.left() < img_rect.left():
                dx = img_rect.left() - rect.left()
            elif rect.right() > img_rect.right():
                dx = img_rect.right() - rect.right()
            dy = 0
            if rect.top() < img_rect.top():
                dy = img_rect.top() - rect.top()
            elif rect.bottom() > img_rect.bottom():
                dy = img_rect.bottom() - rect.bottom()
            rect = rect.translated(dx, dy)

        reference = reference if reference is not None else rect
        for i, other in enumerate(self._committed_rects):
            if i != exclude_idx:
                rect = self._push_to_avoid_overlap(rect, other, reference)
        return rect

    def _push_to_avoid_overlap(self, rect: QRect, other: QRect, reference: QRect) -> QRect:
        """Slides `rect` (without resizing it) just clear of `other`, along
        whichever axis needs the smaller nudge - same one-overlap-at-a-time
        heuristic as _shrink_to_avoid_overlap. Which side to push back
        towards is decided from `reference` (see _clamp_move's docstring),
        not from `rect`'s own current (possibly deeply overlapping) position."""
        overlap = rect.intersected(other)
        if overlap.isEmpty():
            return rect
        if overlap.width() <= overlap.height():
            if reference.center().x() < other.center().x():
                dx = other.left() - rect.right() - 1
            else:
                dx = other.right() - rect.left() + 1
            return rect.translated(dx, 0)
        else:
            if reference.center().y() < other.center().y():
                dy = other.top() - rect.bottom() - 1
            else:
                dy = other.bottom() - rect.top() + 1
            return rect.translated(0, dy)

    def _resize_rect(self, orig: QRect, handle: str, delta: QPoint) -> QRect:
        left, top, right, bottom = orig.left(), orig.top(), orig.right(), orig.bottom()
        if "l" in handle:
            left += delta.x()
        if "r" in handle:
            right += delta.x()
        if "t" in handle:
            top += delta.y()
        if "b" in handle:
            bottom += delta.y()
        return QRect(QPoint(left, top), QPoint(right, bottom)).normalized()

    def _handle_anchors(self, rect: QRect) -> dict:
        return {
            "tl": rect.topLeft(),
            "tr": rect.topRight(),
            "bl": rect.bottomLeft(),
            "br": rect.bottomRight(),
            "t": QPoint(rect.center().x(), rect.top()),
            "b": QPoint(rect.center().x(), rect.bottom()),
            "l": QPoint(rect.left(), rect.center().y()),
            "r": QPoint(rect.right(), rect.center().y()),
        }

    def _handle_at(self, pos: QPoint, rect: QRect) -> str | None:
        tol = self.HANDLE_TOLERANCE
        # Corners first (and only within their own tolerance box) since a
        # point near a corner is also near two edges at once - it should
        # resize diagonally, not collapse to whichever edge check runs first.
        for name in ("tl", "tr", "bl", "br"):
            if (pos - self._handle_anchors(rect)[name]).manhattanLength() <= tol:
                return name
        # Otherwise, anywhere along an edge (not just its midpoint handle)
        # resizes that edge - the midpoint squares drawn in _draw_handles
        # are just a visual affordance, not the only grabbable spot.
        if rect.top() - tol <= pos.y() <= rect.bottom() + tol:
            if abs(pos.x() - rect.left()) <= tol:
                return "l"
            if abs(pos.x() - rect.right()) <= tol:
                return "r"
        if rect.left() - tol <= pos.x() <= rect.right() + tol:
            if abs(pos.y() - rect.top()) <= tol:
                return "t"
            if abs(pos.y() - rect.bottom()) <= tol:
                return "b"
        return None

    def _find_handle_near(self, pos: QPoint) -> tuple[int | None, str | None]:
        for i in reversed(range(len(self._committed_rects))):
            handle = self._handle_at(pos, self._committed_rects[i])
            if handle:
                return i, handle
        return None, None

    def _rect_at(self, pos: QPoint) -> int | None:
        for i in reversed(range(len(self._committed_rects))):
            if self._committed_rects[i].contains(pos):
                return i
        return None

    def _source_rect_for(self, rect: QRect) -> list[int]:
        """Converts a just-edited display rect to source coordinates for
        _committed_source_rects - the one-time, drift-free conversion that
        keeps that list authoritative (see its definition in __init__)."""
        return self._display_rect_to_source(rect, self._displayed_image_rect()) or [0, 0, 1, 1]

    def _display_point_to_source(self, pt: QPoint) -> tuple[int, int]:
        """Position-only counterpart to _display_rect_to_source, for
        callers (like paste placement) that need to place a rect of an
        already-known exact source size rather than convert one wholesale."""
        img_rect = self._displayed_image_rect()
        scale_x = self._original_size[0] / img_rect.width()
        scale_y = self._original_size[1] / img_rect.height()
        x = round((pt.x() - img_rect.left()) * scale_x)
        y = round((pt.y() - img_rect.top()) * scale_y)
        return x, y

    def _place_pasted_rect(self, pos: QPoint) -> None:
        size = self._pending_paste_size
        self._pending_paste_size = None
        self.unsetCursor()
        if size is None:
            return
        src_w, src_h = size

        # Approximate display size, just to reuse the existing bounds/
        # overlap clamping machinery (which operates in display space) for
        # *positioning*. The actual pasted width/height always comes from
        # the exact copied source size below, never from this
        # approximation - so a paste reproduces the copied dimensions
        # exactly regardless of the zoom level at paste time or where it
        # lands relative to the image origin (both of which affect
        # display<->source rounding, and previously made "copy size"
        # silently drift by a pixel or two from what was actually copied).
        img_rect = self._displayed_image_rect()
        approx_w = max(1, round(src_w * img_rect.width() / self._original_size[0]))
        approx_h = max(1, round(src_h * img_rect.height() / self._original_size[1]))
        top_left = QPoint(pos.x() - approx_w // 2, pos.y() - approx_h // 2)
        placed = self._clamp_move(QRect(top_left, QSize(approx_w, approx_h)))

        src_left, src_top = self._display_point_to_source(placed.topLeft())
        src_left = max(0, min(src_left, self._original_size[0] - src_w))
        src_top = max(0, min(src_top, self._original_size[1] - src_h))
        source_rect = [src_left, src_top, src_left + src_w, src_top + src_h]
        display_rect = self._source_rect_to_display(source_rect) or placed

        if self.selection_mode == "crop":
            self._committed_rects = [display_rect]
            self._committed_source_rects = [source_rect]
            self._selected_rect_idx = 0
        else:
            self._committed_rects.append(display_rect)
            self._committed_source_rects.append(source_rect)
            self._selected_rect_idx = len(self._committed_rects) - 1
        self.update()

    def _exec_context_menu(self, menu: QMenu, global_pos: QPoint):
        # Thin seam so tests can drive the menu choice without invoking a
        # real modal event loop - QMenu.exec is a C++ vtable method that
        # Shiboken won't let plain monkeypatching intercept, but this
        # ordinary Python method (defined on our own class) can be.
        return menu.exec(global_pos)

    def _show_context_menu(self, global_pos: QPoint, idx: int) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction("Delete selection")
        copy_action = menu.addAction("Copy size for new selection")
        chosen = self._exec_context_menu(menu, global_pos)
        if chosen == delete_action:
            del self._committed_rects[idx]
            del self._committed_source_rects[idx]
            if self._selected_rect_idx is not None:
                if self._selected_rect_idx == idx:
                    self._selected_rect_idx = None
                elif self._selected_rect_idx > idx:
                    self._selected_rect_idx -= 1
            self.update()
        elif chosen == copy_action:
            src = self._committed_source_rects[idx]
            self._pending_paste_size = (src[2] - src[0], src[3] - src[1])
            self.setCursor(Qt.CrossCursor)

    def _prompt_for_dimensions(self, cur_w: int, cur_h: int) -> tuple[int, int] | None:
        # QInputDialog.getInt is a static factory method, not tied to a
        # per-instance C++ vtable the way QDialog.exec()/QMenu.exec() are -
        # tests can monkeypatch it directly without needing a seam method.
        max_w, max_h = self._original_size
        new_w, ok = QInputDialog.getInt(self, "Selection width", "Width (px):", cur_w, 1, max_w)
        if not ok:
            return None
        new_h, ok = QInputDialog.getInt(self, "Selection height", "Height (px):", cur_h, 1, max_h)
        if not ok:
            return None
        return new_w, new_h

    def _edit_dimensions(self, idx: int) -> None:
        """Double-clicking a selection's border (anywhere along it, not
        just a handle - see _handle_at) opens a numeric prompt to type an
        exact width/height instead of dragging by eye. The top-left corner
        stays anchored; the result is clamped to the image bounds and other
        selections exactly like any other resize (see _clamp_edges)."""
        src = self._committed_source_rects[idx]
        cur_w, cur_h = src[2] - src[0], src[3] - src[1]
        result = self._prompt_for_dimensions(cur_w, cur_h)
        if result is None:
            return
        new_w, new_h = result
        new_w = max(1, min(new_w, self._original_size[0]))
        new_h = max(1, min(new_h, self._original_size[1]))
        left = max(0, min(src[0], self._original_size[0] - new_w))
        top = max(0, min(src[1], self._original_size[1] - new_h))
        desired_display = self._source_rect_to_display([left, top, left + new_w, top + new_h])
        if desired_display is None:
            return
        clamped_display = self._clamp_edges(desired_display, exclude_idx=idx)
        self._committed_rects[idx] = clamped_display
        self._committed_source_rects[idx] = self._source_rect_for(clamped_display)
        self._selected_rect_idx = idx
        self.update()

    def _update_hover(self, pos: QPoint, event) -> None:
        if self._pending_paste_size is not None:
            self.setCursor(Qt.CrossCursor)
            return
        handle_idx, handle_name = self._find_handle_near(pos)
        if handle_name is not None:
            self.setCursor(CURSOR_FOR_HANDLE[handle_name])
            self._set_hover_rect(handle_idx, event)
            return
        idx = self._rect_at(pos)
        if idx is not None:
            self.setCursor(Qt.SizeAllCursor)
            self._set_hover_rect(idx, event)
            return
        self.unsetCursor()
        self._set_hover_rect(None, event)

    def _set_hover_rect(self, idx: int | None, event) -> None:
        self._hover_idx = idx
        if idx is not None and self._original_size is not None:
            # Read straight from the canonical source-space ground truth
            # (see _committed_source_rects in __init__) rather than
            # re-deriving it from the current display rect - the display
            # rect is only ever an *approximation* of the source rect
            # (rounded to the nearest display pixel), so re-converting it
            # back to source space here would show a width/height that
            # wobbles by a pixel or two as the zoom level changes, even
            # though the actual stored/exported selection never moves.
            src = self._committed_source_rects[idx]
            width, height = src[2] - src[0], src[3] - src[1]
            QToolTip.showText(event.globalPosition().toPoint(), f"{width} × {height} px", self)
            return
        QToolTip.hideText()

    def mousePressEvent(self, event) -> None:
        if self.selection_mode == "none":
            super().mousePressEvent(event)
            return
        pos = event.position().toPoint()

        if event.button() == Qt.RightButton:
            if self._pending_paste_size is not None:
                self._pending_paste_size = None
                self.unsetCursor()
                return
            idx = self._rect_at(pos)
            if idx is not None:
                self._selected_rect_idx = idx
                self.update()
                self._show_context_menu(event.globalPosition().toPoint(), idx)
            return

        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        if self._pending_paste_size is not None:
            self._place_pasted_rect(pos)
            return

        handle_idx, handle_name = self._find_handle_near(pos)
        if handle_name is not None:
            self._selected_rect_idx = handle_idx
            self._drag_mode = f"resize:{handle_name}"
            self._drag_start = pos
            self._drag_orig_rect = QRect(self._committed_rects[handle_idx])
            return

        idx = self._rect_at(pos)
        if idx is not None:
            self._selected_rect_idx = idx
            self._drag_mode = "move"
            self._drag_start = pos
            self._drag_orig_rect = QRect(self._committed_rects[idx])
            self.update()
            return

        if self.selection_mode == "crop":
            self._committed_rects = []
            self._committed_source_rects = []
            self._selected_rect_idx = None
        self._drag_mode = "create"
        self._drag_start = pos
        self._drag_rect = QRect(pos, pos)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if self.selection_mode == "none":
            super().mouseMoveEvent(event)
            return
        pos = event.position().toPoint()

        if self._drag_mode is None:
            self._update_hover(pos, event)
            return

        delta = pos - self._drag_start
        if self._drag_mode == "create":
            self._drag_rect = QRect(self._drag_start, pos).normalized()
        elif self._drag_mode == "move":
            new_rect = self._clamp_move(
                self._drag_orig_rect.translated(delta),
                exclude_idx=self._selected_rect_idx,
                reference=self._drag_orig_rect,
            )
            self._committed_rects[self._selected_rect_idx] = new_rect
            self._committed_source_rects[self._selected_rect_idx] = self._source_rect_for(new_rect)
        elif self._drag_mode.startswith("resize:"):
            handle = self._drag_mode.split(":", 1)[1]
            new_rect = self._clamp_edges(
                self._resize_rect(self._drag_orig_rect, handle, delta), exclude_idx=self._selected_rect_idx
            )
            self._committed_rects[self._selected_rect_idx] = new_rect
            self._committed_source_rects[self._selected_rect_idx] = self._source_rect_for(new_rect)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.selection_mode == "none" or self._drag_mode is None:
            super().mouseReleaseEvent(event)
            return
        if self._drag_mode == "create":
            if self._drag_rect is not None and self._drag_rect.width() >= 5 and self._drag_rect.height() >= 5:
                clamped = self._clamp_edges(self._drag_rect)
                if self.selection_mode == "crop":
                    self._committed_rects = [clamped]
                    self._committed_source_rects = [self._source_rect_for(clamped)]
                    self._selected_rect_idx = 0
                else:
                    self._committed_rects.append(clamped)
                    self._committed_source_rects.append(self._source_rect_for(clamped))
                    self._selected_rect_idx = len(self._committed_rects) - 1
            self._drag_rect = None
        self._drag_mode = None
        self._drag_start = None
        self._drag_orig_rect = None
        self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        if self.selection_mode == "none" or event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        pos = event.position().toPoint()
        idx, handle_name = self._find_handle_near(pos)
        if handle_name is None:
            super().mouseDoubleClickEvent(event)
            return
        # The preceding single click (part of the same double-click) may
        # have started a drag - cancel it before opening a modal dialog.
        self._drag_mode = None
        self._drag_start = None
        self._drag_orig_rect = None
        self._edit_dimensions(idx)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.selection_mode == "none":
            return

        painter = QPainter(self)
        img_rect = self._displayed_image_rect()

        if self.selection_mode == "crop" and self._committed_rects:
            crop_rect = self._committed_rects[0]
            overlay_path = QPainterPath()
            overlay_path.addRect(QRectF(img_rect))
            inner_path = QPainterPath()
            inner_path.addRect(QRectF(crop_rect))
            painter.fillPath(overlay_path.subtracted(inner_path), QColor(0, 0, 0, 140))
            self._draw_selection_border(painter, crop_rect, QColor(255, 255, 255), 2)
            self._draw_handles(painter, crop_rect, QColor(255, 255, 255))
        elif self.selection_mode == "split":
            for i, rect in enumerate(self._committed_rects):
                color = SELECTION_COLORS[i % len(SELECTION_COLORS)]
                is_selected = i == self._selected_rect_idx
                self._draw_selection_border(painter, rect, color, 3 if is_selected else 2)
                painter.fillRect(rect.x(), rect.y(), 20, 20, color)
                painter.setPen(QColor(0, 0, 0))
                painter.drawText(QRect(rect.x(), rect.y(), 20, 20), Qt.AlignCenter, str(i + 1))
                if is_selected:
                    self._draw_handles(painter, rect, color)

        if self._drag_rect is not None:
            painter.setPen(QPen(QColor(255, 255, 255), 1, Qt.DashLine))
            painter.drawRect(self._drag_rect)
        painter.end()

    def _draw_selection_border(self, painter: QPainter, rect: QRect, color: QColor, width: int) -> None:
        """Draws the selection outline as explicit filled bands, not a
        QPainter.drawRect() stroke - a stroke is centered on the path by
        default, which would cover the true boundary pixels, and Qt's
        pixel rounding for an even-width stroke also isn't symmetric
        between edges (the right/bottom edge ends up a pixel further from
        the rect than the left/top edge does). Filling exact pixel bands
        avoids both problems: `rect` stays completely untouched, with the
        border landing flush against its outside on all four sides
        equally - everything inside `rect` is the selection, the border
        marks the outside and isn't part of it."""
        outer = QRect(
            rect.left() - width, rect.top() - width,
            rect.width() + 2 * width, rect.height() + 2 * width,
        )
        self._fill_frame(painter, outer, width, color)

    def _fill_frame(self, painter: QPainter, rect: QRect, thickness: int, brush: QColor) -> None:
        """Fills a `thickness`-px frame just inside `rect`'s own edges."""
        painter.fillRect(rect.left(), rect.top(), rect.width(), thickness, brush)
        painter.fillRect(rect.left(), rect.bottom() - thickness + 1, rect.width(), thickness, brush)
        painter.fillRect(rect.left(), rect.top(), thickness, rect.height(), brush)
        painter.fillRect(rect.right() - thickness + 1, rect.top(), thickness, rect.height(), brush)

    def _draw_handles(self, painter: QPainter, rect: QRect, color: QColor) -> None:
        size = 6
        painter.setPen(QPen(color, 1))
        painter.setBrush(QColor(255, 255, 255))
        for pt in self._handle_anchors(rect).values():
            painter.drawRect(pt.x() - size // 2, pt.y() - size // 2, size, size)


class PreviewScrollArea(QScrollArea):
    """Wraps an ImagePreviewLabel so a manual zoom level has scrollbars to
    pan with. Also feeds the label its real viewport size on every resize -
    the label needs that to compute "fit" scale correctly even right after
    zooming back out, when its own size is still pinned to the zoomed
    (larger) pixmap and no longer reflects the space actually available."""

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        widget = self.widget()
        if isinstance(widget, ImagePreviewLabel):
            widget.set_viewport_hint(self.viewport().size())


class CollapsibleSection(QWidget):
    """A clearly-labeled section that expands/collapses on click.

    Replaces the previous QSplitter-based right panel: dragging splitter
    handles between panes of very different natural sizes (a list, a form,
    a text box) is a known Qt pain point - handles fight the children's
    minimum size hints and can appear to "not respond." A click-to-toggle
    header is unambiguous: it's either fully shown or fully hidden, no
    partial-drag state to get stuck in.
    """

    def __init__(self, title: str, content: QWidget, expanded: bool = True, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.content = content

        self.toggle_button = QToolButton(self)
        self.toggle_button.setText(title)
        if tooltip:
            self.toggle_button.setToolTip(tooltip)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle_button.setStyleSheet("QToolButton { font-weight: bold; border: none; }")
        self.toggle_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.toggle_button.clicked.connect(self._on_toggled)

        self.content.setVisible(expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

    def _on_toggled(self, checked: bool) -> None:
        self.content.setVisible(checked)
        self.toggle_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)


class BookmarkListWidget(QListWidget):
    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item is None:
            return
        bookmark_index = item.data(Qt.UserRole)
        mime = QMimeData()
        mime.setData(BOOKMARK_MIME, str(bookmark_index).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)


class PageThumbnailGrid(QListWidget):
    bookmarkDropped = Signal(int, int)  # bookmark_index, page_index
    pageMoveRequested = Signal(int, int)  # old_page_index, new_page_index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setIconSize(QSize(100, 150))
        self.setSpacing(6)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setAcceptDrops(True)
        # DragDrop (not DropOnly): a thumbnail can now also be dragged OUT to
        # reorder pages, in addition to accepting bookmark drops from
        # BookmarkListWidget. startDrag/dropEvent are fully overridden below,
        # so Qt's built-in InternalMove auto-reordering never runs - which
        # of our two custom mime types is present decides page-move vs.
        # bookmark-placement ourselves.
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item is None:
            return
        page_index = item.data(Qt.UserRole)
        mime = QMimeData()
        mime.setData(PAGE_MIME, str(page_index).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(BOOKMARK_MIME) or event.mimeData().hasFormat(PAGE_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(BOOKMARK_MIME) or event.mimeData().hasFormat(PAGE_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        target_item = self.itemAt(event.position().toPoint())

        if mime.hasFormat(BOOKMARK_MIME):
            bookmark_index = int(bytes(mime.data(BOOKMARK_MIME)).decode("utf-8"))
            if target_item is not None:
                page_index = target_item.data(Qt.UserRole)
                self.bookmarkDropped.emit(bookmark_index, page_index)
            event.acceptProposedAction()
        elif mime.hasFormat(PAGE_MIME):
            old_index = int(bytes(mime.data(PAGE_MIME)).decode("utf-8"))
            if target_item is not None:
                new_index = target_item.data(Qt.UserRole)
                if new_index != old_index:
                    self.pageMoveRequested.emit(old_index, new_index)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
