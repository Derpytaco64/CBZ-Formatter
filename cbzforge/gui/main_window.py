from __future__ import annotations

import copy
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from cbzforge.bookmarks import suggest_bookmarks
from cbzforge.gui.thumbnails import (
    load_original_pixmap_and_size,
    load_page_bytes,
    load_preview_pixmap,
    load_thumbnail_icon,
)
from cbzforge.gui.widgets import (
    BookmarkListWidget,
    CollapsibleSection,
    FlowLayout,
    ImagePreviewLabel,
    PageThumbnailGrid,
    PreviewScrollArea,
)
from cbzforge.manifest import Bookmark, Manifest
from cbzforge.pack import build_cbz
from cbzforge.page_ops import (
    apply_crop_box,
    move_page,
    reset_crop,
    resolve_output_pages,
    set_excluded,
    split_page,
)
from cbzforge.profiles import SeriesProfile, load_or_create, profile_path
from cbzforge.scan import build_manifest
from cbzforge.xmlgen import SeriesMetadata, build_comicinfo_xml, validate_comicinfo_xml

BOOKMARK_TYPES = ["FrontCover", "Story", "Other", "Editorial", "BackCover"]

DEFAULT_PROFILES_DIR = Path.home() / ".cbzforge" / "profiles"

STATUS_COLOR_OK = "#2e7d32"
STATUS_COLOR_FAIL = "#c62828"
STATUS_COLOR_BUSY = "palette(text)"

MAX_UNDO_DEPTH = 20


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


class BookmarkEditDialog(QDialog):
    def __init__(self, parent=None, bookmark_type: str = "Story", label: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Bookmark")
        layout = QFormLayout(self)

        self.type_combo = QComboBox(self)
        self.type_combo.addItems(BOOKMARK_TYPES)
        self.type_combo.setCurrentText(bookmark_type)
        layout.addRow("Type", self.type_combo)

        self.label_edit = QLineEdit(label, self)
        layout.addRow("Label", self.label_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> tuple[str, str]:
        return self.type_combo.currentText(), self.label_edit.text()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CBZ Forge")
        icon_path = Path(__file__).parent / "resources" / "app_icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.manifest: Manifest | None = None
        self.profiles_dir = DEFAULT_PROFILES_DIR
        self._mangadex_chapters: list[dict] = []
        self._undo_stack: list[Manifest] = []

        self._build_ui()
        self._build_menu()

    # ---- UI construction -------------------------------------------------

    def _build_ui(self) -> None:
        preview_panel = self._build_preview_panel()
        thumbnail_panel = self._build_thumbnail_panel()

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.addWidget(
            CollapsibleSection(
                "Table of Contents",
                self._build_bookmark_section(),
                expanded=True,
                tooltip="Drag a row onto a page thumbnail to place it there.",
            )
        )
        right_layout.addWidget(
            CollapsibleSection(
                "Online Metadata Lookup",
                self._build_lookup_section(),
                expanded=True,
                tooltip="Look up series/chapter info from MangaDex and AniList.",
            )
        )
        right_layout.addWidget(
            CollapsibleSection(
                "Volume Metadata",
                self._build_metadata_form(),
                expanded=True,
                tooltip="Fields written into ComicInfo.xml.",
            )
        )
        right_layout.addWidget(
            CollapsibleSection(
                "OCR Assist",
                self._build_ocr_section(),
                expanded=False,
                tooltip="Extract plain-print text from the selected page (not reliable on stylized chapter-title art).",
            )
        )
        right_layout.addStretch(1)

        # Sections toggle visibility with a plain click instead of a
        # draggable splitter handle - a splitter here previously fought the
        # very different natural sizes of a list vs. a form vs. a text box
        # and could feel unresponsive when trying to resize a section. The
        # whole stack sits in a scroll area so it scrolls as one unit
        # whenever the expanded sections are collectively taller than the
        # window, regardless of how many are open at once.
        self.right_scroll_area = QScrollArea()
        self.right_scroll_area.setWidgetResizable(True)
        self.right_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.right_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.right_scroll_area.setWidget(right_panel)

        build_btn = QPushButton("Build CBZ...")
        build_btn.setMinimumHeight(36)
        build_btn.clicked.connect(self._build_clicked)

        right_container = QWidget()
        right_container_layout = QVBoxLayout(right_container)
        right_container_layout.addWidget(self.right_scroll_area)
        right_container_layout.addWidget(build_btn)

        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.addWidget(thumbnail_panel)
        main_splitter.addWidget(preview_panel)
        main_splitter.addWidget(right_container)
        main_splitter.setSizes([350, 600, 500])

        self.setCentralWidget(main_splitter)
        self.setStatusBar(QStatusBar())

    def _build_thumbnail_panel(self) -> QWidget:
        self.thumbnail_grid = PageThumbnailGrid()
        self.thumbnail_grid.bookmarkDropped.connect(self.move_bookmark)
        self.thumbnail_grid.pageMoveRequested.connect(self._move_page)
        self.thumbnail_grid.itemSelectionChanged.connect(self._on_page_selected)

        hint = QLabel(
            "Drag a page onto another to reorder it, or select a page and "
            "type its new position below."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(mid); font-size: 11px;")

        move_label = QLabel("Move selected page to position:")
        move_label.setWordWrap(True)
        self.move_to_spin = QSpinBox()
        self.move_to_spin.setRange(0, 0)
        move_btn = QPushButton("Move")
        move_btn.clicked.connect(self._move_selected_page_to_position)

        move_row = QWidget()
        move_row_layout = FlowLayout(move_row, spacing=4)
        move_row_layout.addWidget(move_label)
        move_row_layout.addWidget(self.move_to_spin)
        move_row_layout.addWidget(move_btn)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(hint)
        layout.addWidget(move_row)
        layout.addWidget(self.thumbnail_grid, 1)
        return container

    def _build_preview_panel(self) -> QWidget:
        self.preview_header = QLabel("No page selected")
        self.preview_header.setStyleSheet("font-weight: bold;")
        self.preview_header.setWordWrap(True)
        self.preview_label = ImagePreviewLabel()
        self.preview_label.zoomChanged.connect(self._on_zoom_changed)

        self.preview_scroll = PreviewScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setAlignment(Qt.AlignCenter)
        self.preview_scroll.setWidget(self.preview_label)

        zoom_out_btn = QPushButton("Zoom -")
        zoom_out_btn.setToolTip("Zoom out (Ctrl+scroll wheel also works).")
        zoom_out_btn.clicked.connect(self.preview_label.zoom_out)
        zoom_in_btn = QPushButton("Zoom +")
        zoom_in_btn.setToolTip("Zoom in for more precise crop/split placement (Ctrl+scroll wheel also works).")
        zoom_in_btn.clicked.connect(self.preview_label.zoom_in)
        zoom_fit_btn = QPushButton("Fit")
        zoom_fit_btn.setToolTip("Reset zoom to fit the whole page in the preview pane.")
        zoom_fit_btn.clicked.connect(self.preview_label.zoom_reset)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(40)

        zoom_row = QWidget()
        zoom_row_layout = FlowLayout(zoom_row, spacing=4)
        for w in (zoom_out_btn, self.zoom_label, zoom_in_btn, zoom_fit_btn):
            zoom_row_layout.addWidget(w)

        self.crop_btn = QPushButton("Crop")
        self.crop_btn.setCheckable(True)
        self.crop_btn.setToolTip("Drag a rectangle on the page above, then Apply.")
        self.crop_btn.toggled.connect(self._on_crop_btn_toggled)

        self.split_btn = QPushButton("Split")
        self.split_btn.setCheckable(True)
        self.split_btn.setToolTip(
            "Drag multiple rectangles on the page above, then Apply - each becomes its own page."
        )
        self.split_btn.toggled.connect(self._on_split_btn_toggled)

        apply_btn = QPushButton("Apply")
        apply_btn.setToolTip("Commit the drawn crop/split selection.")
        apply_btn.clicked.connect(self._apply_edit_selection)

        clear_btn = QPushButton("Clear Selection")
        clear_btn.setToolTip("Discard the drawn rectangle(s) without applying them.")
        clear_btn.clicked.connect(lambda: self.preview_label.clear_selection())

        reset_crop_btn = QPushButton("Reset Crop")
        reset_crop_btn.setToolTip("Undo this page's crop, back to the original image.")
        reset_crop_btn.clicked.connect(self._reset_crop_selected)

        self.exclude_btn = QPushButton("Exclude This Page")
        self.exclude_btn.setCheckable(True)
        self.exclude_btn.setToolTip(
            "Grey this page out and skip it from the final build - reversible any time."
        )
        self.exclude_btn.toggled.connect(self._on_exclude_btn_toggled)

        undo_btn = QPushButton("Undo Last Edit")
        undo_btn.setToolTip("Undo the last move/crop/split/exclude action.")
        undo_btn.clicked.connect(self._undo)

        toolbar_row = QWidget()
        toolbar_layout = FlowLayout(toolbar_row, spacing=4)
        for btn in (
            self.crop_btn,
            self.split_btn,
            apply_btn,
            clear_btn,
            reset_crop_btn,
            self.exclude_btn,
            undo_btn,
        ):
            toolbar_layout.addWidget(btn)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(self.preview_header)
        layout.addWidget(self.preview_scroll, 1)
        layout.addWidget(zoom_row)
        layout.addWidget(toolbar_row)
        return container

    def _on_zoom_changed(self, _scale: float) -> None:
        self.zoom_label.setText(f"{self.preview_label.zoom_percent()}%")

    def _build_bookmark_section(self) -> QWidget:
        self.bookmark_list = BookmarkListWidget()
        self.bookmark_list.setDragEnabled(True)
        self.bookmark_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.bookmark_list.setWordWrap(True)
        self.bookmark_list.itemDoubleClicked.connect(self._edit_selected_bookmark)
        self.bookmark_list.setToolTip(
            "Drag a row onto a page thumbnail on the left to move it there.\n"
            "Double-click a row to edit its type/label."
        )
        # Bounded so a long TOC scrolls within its own list instead of
        # stretching to its full content height and pushing every other
        # section far down the outer scroll area.
        self.bookmark_list.setMaximumHeight(260)

        add_btn = QPushButton("Add at selected page")
        add_btn.clicked.connect(self._add_bookmark)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected_bookmark)
        resuggest_btn = QPushButton("Reset to scan guess")
        resuggest_btn.setToolTip(
            "Discards manual placement and re-runs the automatic filename-based guess."
        )
        resuggest_btn.clicked.connect(self._resuggest_bookmarks)

        buttons_widget = QWidget()
        buttons_layout = FlowLayout(buttons_widget, spacing=4)
        buttons_layout.addWidget(add_btn)
        buttons_layout.addWidget(remove_btn)
        buttons_layout.addWidget(resuggest_btn)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.bookmark_list)
        layout.addWidget(buttons_widget)
        return container

    def _build_lookup_section(self) -> QWidget:
        lookup_btn = QPushButton("Look up metadata online (uses the Series field below)")
        lookup_btn.clicked.connect(self._lookup_metadata_online)

        self.lookup_status_mangadex = QLabel("MangaDex: not checked yet")
        self.lookup_status_anilist = QLabel("AniList: not checked yet")
        for label in (self.lookup_status_mangadex, self.lookup_status_anilist):
            label.setWordWrap(True)

        self.lookup_staff_output = QPlainTextEdit()
        self.lookup_staff_output.setReadOnly(True)
        self.lookup_staff_output.setMaximumHeight(70)
        self.lookup_staff_output.setPlaceholderText(
            "AniList staff credits appear here after a successful lookup - "
            "copy the right name into Writer/Penciller/Inker/Cover Artist "
            "below yourself. Role names vary too much between series to "
            "auto-assign safely."
        )

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(lookup_btn)
        layout.addWidget(self.lookup_status_mangadex)
        layout.addWidget(self.lookup_status_anilist)
        layout.addWidget(self.lookup_staff_output)
        return container

    def _build_ocr_section(self) -> QWidget:
        self.ocr_output = QPlainTextEdit()
        self.ocr_output.setReadOnly(True)
        self.ocr_output.setPlaceholderText(
            "Select a page on the left, then click \"Run OCR on selected page\" "
            "to extract plain-print text (table of contents / credits pages "
            "only - not reliable on stylized chapter-title art)."
        )
        ocr_btn = QPushButton("Run OCR on selected page")
        ocr_btn.clicked.connect(self._run_ocr_on_selected)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(ocr_btn)
        layout.addWidget(self.ocr_output)
        return container

    def _build_metadata_form(self) -> QWidget:
        box = QGroupBox()
        layout = QFormLayout(box)
        # A row wraps onto two lines (label above field) if it doesn't fit
        # the panel's current width, instead of clipping the field.
        layout.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.field_series = QLineEdit()
        self.field_series.setToolTip(
            "Series name - must be spelled identically across every volume "
            "so 'Load series profile' finds the right one."
        )
        self.field_volume = QSpinBox()
        self.field_volume.setRange(0, 999)
        self.field_volume.setToolTip("Volume number of this book, e.g. 1.")
        self.field_number = QLineEdit()
        self.field_number.setToolTip(
            "ComicInfo 'Number' field - the series-position number some "
            "readers sort/group by, e.g. '1.0' for volume 1. Usually matches Volume."
        )
        self.field_title = QLineEdit()
        self.field_title.setToolTip(
            "Display title shown by readers like Calibre - must stand alone, "
            "e.g. 'Kagurabachi Vol. 1', not just '1' or 'Volume 1'."
        )
        self.field_summary = QPlainTextEdit()
        self.field_summary.setMaximumHeight(60)
        self.field_notes = QPlainTextEdit()
        self.field_notes.setMaximumHeight(40)
        self.field_notes.setToolTip(
            "Record any corrections made here (filename vs. official chapter "
            "titles, guessed vs. confirmed dates, etc.) - never silently fix without noting it."
        )
        self.field_year = QSpinBox()
        self.field_year.setRange(0, 2100)
        self.field_month = QSpinBox()
        self.field_month.setRange(0, 12)
        self.field_day = QSpinBox()
        self.field_day.setRange(0, 31)
        self.field_writer = QLineEdit()
        self.field_penciller = QLineEdit()
        self.field_inker = QLineEdit()
        self.field_cover_artist = QLineEdit()
        self.field_publisher = QLineEdit()
        self.field_imprint = QLineEdit()
        self.field_imprint.setToolTip(
            "Publisher imprint, e.g. 'SHONEN JUMP' - can change partway "
            "through a long-running series, check rather than assume."
        )
        self.field_genre = QLineEdit()
        self.field_genre.setToolTip("Comma-separated, e.g. 'Action, Fantasy'.")
        self.field_language_iso = QLineEdit("en")
        self.field_language_iso.setToolTip("ISO 639-1 code of this release's text, e.g. 'en'.")
        self.field_age_rating = QLineEdit()
        self.field_age_rating.setToolTip("ComicInfo AgeRating value, e.g. 'Teen', 'Mature 17+'.")
        self.field_black_and_white = QComboBox()
        self.field_black_and_white.addItems(["Yes", "No", "Unknown"])
        self.field_black_and_white.setToolTip("Almost always 'Yes' for manga interiors.")
        self.field_manga = QComboBox()
        self.field_manga.addItems(["Yes", "YesAndRightToLeft", "No", "Unknown"])
        self.field_manga.setCurrentText("YesAndRightToLeft")
        self.field_manga.setToolTip("Reading direction - 'YesAndRightToLeft' for standard manga.")

        layout.addRow("Series", self.field_series)
        layout.addRow("Volume", self.field_volume)
        layout.addRow("Number", self.field_number)
        layout.addRow("Title", self.field_title)
        layout.addRow("Summary", self.field_summary)
        layout.addRow("Notes", self.field_notes)
        layout.addRow("Year", self.field_year)
        layout.addRow("Month", self.field_month)
        layout.addRow("Day", self.field_day)
        layout.addRow("Writer", self.field_writer)
        layout.addRow("Penciller", self.field_penciller)
        layout.addRow("Inker", self.field_inker)
        layout.addRow("Cover Artist", self.field_cover_artist)
        layout.addRow("Publisher", self.field_publisher)
        layout.addRow("Imprint", self.field_imprint)
        layout.addRow("Genre", self.field_genre)
        layout.addRow("Language ISO", self.field_language_iso)
        layout.addRow("Age Rating", self.field_age_rating)
        layout.addRow("Black && White", self.field_black_and_white)
        layout.addRow("Manga", self.field_manga)

        load_profile_btn = QPushButton("Load series profile")
        load_profile_btn.setToolTip(
            "Fill Writer/Publisher/Imprint/etc. from a profile saved for this "
            "series name on an earlier volume."
        )
        load_profile_btn.clicked.connect(self._load_profile_clicked)
        save_profile_btn = QPushButton("Save series profile")
        save_profile_btn.setToolTip(
            "Save the fields below under this series name for reuse on the next volume."
        )
        save_profile_btn.clicked.connect(self._save_profile_clicked)

        profile_buttons_widget = QWidget()
        profile_buttons_layout = FlowLayout(profile_buttons_widget, spacing=4)
        profile_buttons_layout.addWidget(load_profile_btn)
        profile_buttons_layout.addWidget(save_profile_btn)
        layout.addRow(profile_buttons_widget)

        return box

    def _build_menu(self) -> None:
        toolbar = QToolBar()
        self.addToolBar(toolbar)

        open_cbz_action = toolbar.addAction("Open CBZ/ZIP...")
        open_cbz_action.triggered.connect(self._open_cbz)

        open_folder_action = toolbar.addAction("Open Image Folder...")
        open_folder_action.triggered.connect(self._open_folder)

        save_manifest_action = toolbar.addAction("Save Manifest...")
        save_manifest_action.triggered.connect(self._save_manifest)

        load_manifest_action = toolbar.addAction("Load Manifest...")
        load_manifest_action.triggered.connect(self._load_manifest)

    # ---- source loading ----------------------------------------------

    def _open_cbz(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CBZ/ZIP", "", "Comic archives (*.cbz *.zip)"
        )
        if path:
            self._load_source(path)

    def _open_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open Image Folder")
        if path:
            self._load_source(path)

    def _load_source(self, path: str) -> None:
        self.manifest = build_manifest(path)
        if self.manifest.corruption and not self.manifest.corruption.clean:
            QMessageBox.warning(
                self,
                "Corrupt entries found",
                "The following entries could not be read and were skipped:\n"
                + "\n".join(self.manifest.corruption.bad_entries),
            )
        self.manifest.bookmarks = suggest_bookmarks(self.manifest)
        self._undo_stack.clear()
        self._populate_thumbnails()
        self._refresh_bookmark_list()
        self.statusBar().showMessage(
            f"Loaded {self.manifest.page_count} pages "
            f"(parser: {self.manifest.parser_used})"
        )

    def _populate_thumbnails(self) -> None:
        self.thumbnail_grid.clear()
        self.preview_label.clear_preview()
        self.preview_header.setText("No page selected")
        self._reset_tool_ui()
        assert self.manifest is not None
        self.move_to_spin.setRange(0, max(0, len(self.manifest.pages) - 1))
        for page in self.manifest.pages:
            icon = load_thumbnail_icon(self.manifest, page.index)
            item = QListWidgetItem(icon, str(page.index))
            item.setData(Qt.UserRole, page.index)
            self.thumbnail_grid.addItem(item)

    # ---- manifest load/save -------------------------------------------

    def _save_manifest(self) -> None:
        if self.manifest is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Manifest", "", "YAML (*.yaml)")
        if path:
            self.manifest.save(path)
            self.statusBar().showMessage(f"Manifest saved to {path}")

    def _load_manifest(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Manifest", "", "YAML (*.yaml)")
        if not path:
            return
        self.manifest = Manifest.load(path)
        self._undo_stack.clear()
        self._populate_thumbnails()
        self._refresh_bookmark_list()
        self.statusBar().showMessage(f"Manifest loaded from {path}")

    # ---- undo -----------------------------------------------------------

    def _snapshot_for_undo(self) -> None:
        if self.manifest is None:
            return
        self._undo_stack.append(copy.deepcopy(self.manifest))
        if len(self._undo_stack) > MAX_UNDO_DEPTH:
            self._undo_stack.pop(0)

    def _undo(self) -> None:
        if not self._undo_stack:
            self.statusBar().showMessage("Nothing to undo")
            return
        self.manifest = self._undo_stack.pop()
        self._populate_thumbnails()
        self._refresh_bookmark_list()
        self.statusBar().showMessage("Undid last edit")

    def _reset_tool_ui(self) -> None:
        self.crop_btn.blockSignals(True)
        self.split_btn.blockSignals(True)
        self.crop_btn.setChecked(False)
        self.split_btn.setChecked(False)
        self.crop_btn.blockSignals(False)
        self.split_btn.blockSignals(False)
        self.preview_label.set_selection_mode("none")

    # ---- bookmark list ---------------------------------------------------

    def _refresh_bookmark_list(self) -> None:
        self.bookmark_list.clear()
        if self.manifest is None:
            return
        for i, bookmark in enumerate(self.manifest.bookmarks):
            text = f"[{bookmark.page_index}] {bookmark.type}: {bookmark.label}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, i)
            self.bookmark_list.addItem(item)

    def move_bookmark(self, bookmark_index: int, page_index: int) -> None:
        if self.manifest is None:
            return
        self.manifest.bookmarks[bookmark_index].page_index = page_index
        self._refresh_bookmark_list()

    def _selected_page_index(self) -> int | None:
        items = self.thumbnail_grid.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    def _add_bookmark(self) -> None:
        if self.manifest is None:
            return
        page_index = self._selected_page_index()
        if page_index is None:
            QMessageBox.information(self, "Add bookmark", "Select a page first.")
            return
        dialog = BookmarkEditDialog(self)
        if dialog.exec() == QDialog.Accepted:
            bmk_type, label = dialog.values()
            self.manifest.bookmarks.append(
                Bookmark(page_index=page_index, type=bmk_type, label=label)
            )
            self._refresh_bookmark_list()

    def _remove_selected_bookmark(self) -> None:
        if self.manifest is None:
            return
        items = self.bookmark_list.selectedItems()
        if not items:
            return
        index = items[0].data(Qt.UserRole)
        del self.manifest.bookmarks[index]
        self._refresh_bookmark_list()

    def _edit_selected_bookmark(self, item: QListWidgetItem) -> None:
        if self.manifest is None:
            return
        index = item.data(Qt.UserRole)
        bookmark = self.manifest.bookmarks[index]
        dialog = BookmarkEditDialog(self, bookmark.type, bookmark.label)
        if dialog.exec() == QDialog.Accepted:
            bmk_type, label = dialog.values()
            bookmark.type = bmk_type
            bookmark.label = label
            self._refresh_bookmark_list()

    def _resuggest_bookmarks(self) -> None:
        if self.manifest is None:
            return
        reply = QMessageBox.question(
            self,
            "Re-suggest bookmarks",
            "This replaces all current bookmarks with fresh guesses from the "
            "scan data, discarding any manual placement done so far. Continue?",
        )
        if reply == QMessageBox.Yes:
            self.manifest.bookmarks = suggest_bookmarks(self.manifest)
            self._refresh_bookmark_list()

    # ---- page reordering --------------------------------------------------

    def _move_page(self, old_index: int, new_index: int) -> None:
        if self.manifest is None or old_index == new_index:
            return
        self._snapshot_for_undo()
        move_page(self.manifest, old_index, new_index)
        self._populate_thumbnails()
        self._refresh_bookmark_list()
        clamped = max(0, min(new_index, self.thumbnail_grid.count() - 1))
        if clamped >= 0:
            self.thumbnail_grid.setCurrentRow(clamped)
        self.statusBar().showMessage(f"Moved page {old_index} to position {clamped}")

    def _move_selected_page_to_position(self) -> None:
        if self.manifest is None:
            return
        old_index = self._selected_page_index()
        if old_index is None:
            QMessageBox.information(self, "Move page", "Select a page first.")
            return
        self._move_page(old_index, self.move_to_spin.value())

    # ---- image editing: crop / split / exclude ----------------------------

    def _on_crop_btn_toggled(self, checked: bool) -> None:
        self._set_tool_mode("crop" if checked else "none")

    def _on_split_btn_toggled(self, checked: bool) -> None:
        self._set_tool_mode("split" if checked else "none")

    def _set_tool_mode(self, mode: str) -> None:
        self.crop_btn.blockSignals(True)
        self.split_btn.blockSignals(True)
        self.crop_btn.setChecked(mode == "crop")
        self.split_btn.setChecked(mode == "split")
        self.crop_btn.blockSignals(False)
        self.split_btn.blockSignals(False)
        self.preview_label.set_selection_mode(mode)
        self._refresh_preview_for_current_mode()

    def _refresh_preview_for_current_mode(self) -> None:
        if self.manifest is None:
            return
        page_index = self._selected_page_index()
        if page_index is None:
            return
        if self.preview_label.selection_mode in ("crop", "split"):
            pixmap, original_size = load_original_pixmap_and_size(self.manifest, page_index)
            self.preview_label.set_source_pixmap(pixmap, original_size)
            existing_crop = self.manifest.pages[page_index].crop_box
            if self.preview_label.selection_mode == "crop" and existing_crop:
                # Show the page's current crop as an adjustable box instead
                # of forcing the user to redraw it from scratch.
                self.preview_label.set_committed_rects_from_source([existing_crop])
        else:
            self.preview_label.set_source_pixmap(load_preview_pixmap(self.manifest, page_index))

    def _on_exclude_btn_toggled(self, checked: bool) -> None:
        if self.manifest is None:
            return
        page_index = self._selected_page_index()
        if page_index is None:
            return
        if self.manifest.pages[page_index].excluded == checked:
            return  # programmatic sync from _on_page_selected, not a real user toggle
        self._snapshot_for_undo()
        set_excluded(self.manifest, page_index, checked)
        self._populate_thumbnails()
        if page_index < self.thumbnail_grid.count():
            self.thumbnail_grid.setCurrentRow(page_index)
        self.statusBar().showMessage(
            f"Page {page_index} {'excluded' if checked else 're-included'}"
        )

    def _apply_edit_selection(self) -> None:
        if self.manifest is None:
            return
        page_index = self._selected_page_index()
        if page_index is None:
            return
        mode = self.preview_label.selection_mode
        rects = self.preview_label.get_source_rects()
        if mode not in ("crop", "split") or not rects:
            QMessageBox.information(
                self, "Apply", "Pick Crop or Split, draw a selection on the page, then Apply."
            )
            return

        self._snapshot_for_undo()
        if mode == "crop":
            apply_crop_box(self.manifest, page_index, rects[0])
            message = f"Cropped page {page_index}"
        else:
            if len(rects) < 2:
                reply = QMessageBox.question(
                    self,
                    "Split with one region?",
                    "Only one region is selected - this will just crop the page, "
                    "not split it into multiple pages. Continue?",
                )
                if reply != QMessageBox.Yes:
                    self._undo_stack.pop()
                    return
            split_page(self.manifest, page_index, rects)
            message = (
                f"Split page {page_index} into {len(rects)} pieces - "
                f"double check bookmark placement if this page was bookmarked"
            )

        self._reset_tool_ui()
        self._populate_thumbnails()
        self._refresh_bookmark_list()
        if page_index < self.thumbnail_grid.count():
            self.thumbnail_grid.setCurrentRow(page_index)
        self.statusBar().showMessage(message)

    def _reset_crop_selected(self) -> None:
        if self.manifest is None:
            return
        page_index = self._selected_page_index()
        if page_index is None:
            return
        if self.manifest.pages[page_index].crop_box is None:
            self.statusBar().showMessage("This page has no crop to reset")
            return
        self._snapshot_for_undo()
        reset_crop(self.manifest, page_index)
        self._populate_thumbnails()
        if page_index < self.thumbnail_grid.count():
            self.thumbnail_grid.setCurrentRow(page_index)
        self.statusBar().showMessage(f"Reset crop on page {page_index}")

    # ---- OCR assist -----------------------------------------------------

    def _on_page_selected(self) -> None:
        page_index = self._selected_page_index()
        if page_index is None or self.manifest is None:
            return
        page = self.manifest.pages[page_index]
        self.statusBar().showMessage(f"Page {page_index} selected")

        notes = []
        if page.excluded:
            notes.append("EXCLUDED")
        if page.crop_box:
            notes.append("cropped")
        suffix = f" [{', '.join(notes)}]" if notes else ""
        self.preview_header.setText(f"Page {page_index} of {self.manifest.page_count - 1}{suffix}")

        self.exclude_btn.blockSignals(True)
        self.exclude_btn.setChecked(page.excluded)
        self.exclude_btn.blockSignals(False)

        self._refresh_preview_for_current_mode()

    def _run_ocr_on_selected(self) -> None:
        from cbzforge import ocr

        if self.manifest is None:
            return
        page_index = self._selected_page_index()
        if page_index is None:
            QMessageBox.information(self, "OCR", "Select a page first.")
            return
        if not ocr.is_available():
            QMessageBox.warning(
                self,
                "OCR unavailable",
                "The bundled OCR engine failed to load. This shouldn't happen "
                "in a normal install - check the terminal for the underlying error.",
            )
            return
        data = load_page_bytes(self.manifest, page_index)
        text = ocr.extract_text(data)
        self.ocr_output.setPlainText(text)
        if ocr.is_probably_credits_page(text):
            self.ocr_output.appendPlainText(
                "\n[looks like a credits/copyright page - consider an "
                "Editorial \"Scanlation Credits\" bookmark here]"
            )
        toc_entries = ocr.parse_toc_text(text)
        if toc_entries:
            lines = "\n".join(f"  {e.printed_page_number}: {e.title}" for e in toc_entries)
            self.ocr_output.appendPlainText(f"\n[parsed as a table of contents]\n{lines}")

    # ---- online metadata lookup ------------------------------------------

    def _set_lookup_status(self, label: QLabel, source: str, message: str, ok: bool | None) -> None:
        if ok is True:
            color, mark = STATUS_COLOR_OK, "OK"
        elif ok is False:
            color, mark = STATUS_COLOR_FAIL, "FAIL"
        else:
            color, mark = STATUS_COLOR_BUSY, "..."
        label.setText(f"{source}: [{mark}] {message}")
        label.setStyleSheet(f"color: {color};")

    def _lookup_metadata_online(self) -> None:
        series_name = self.field_series.text().strip()
        if not series_name:
            QMessageBox.information(self, "Metadata lookup", "Enter a series name first.")
            return

        from cbzforge import metadata_sources

        self._set_lookup_status(self.lookup_status_mangadex, "MangaDex", "looking up...", ok=None)
        self._set_lookup_status(self.lookup_status_anilist, "AniList", "looking up...", ok=None)
        QApplication.processEvents()

        self._lookup_mangadex(metadata_sources, series_name)
        self._lookup_anilist(metadata_sources, series_name)

    def _lookup_mangadex(self, metadata_sources, series_name: str) -> None:
        try:
            results = metadata_sources.mangadex_search_manga(series_name, limit=1)
        except Exception as exc:  # noqa: BLE001 - reported to the user, not swallowed
            self._set_lookup_status(self.lookup_status_mangadex, "MangaDex", f"error - {exc}", ok=False)
            return

        if not results:
            self._set_lookup_status(self.lookup_status_mangadex, "MangaDex", "no match found", ok=False)
            return

        try:
            chapters = metadata_sources.mangadex_chapter_titles(results[0]["id"])
        except Exception as exc:  # noqa: BLE001
            self._set_lookup_status(
                self.lookup_status_mangadex, "MangaDex", f"found series but chapter fetch failed - {exc}", ok=False
            )
            return

        self._mangadex_chapters = chapters
        self._set_lookup_status(
            self.lookup_status_mangadex,
            "MangaDex",
            f"found {results[0]['title']!r} - {len(chapters)} chapters available "
            f"(cross-check chapter titles against these when placing bookmarks)",
            ok=True,
        )

    def _lookup_anilist(self, metadata_sources, series_name: str) -> None:
        try:
            data = metadata_sources.anilist_series_metadata(series_name)
        except Exception as exc:  # noqa: BLE001
            self._set_lookup_status(self.lookup_status_anilist, "AniList", f"error - {exc}", ok=False)
            return

        if not data:
            self._set_lookup_status(self.lookup_status_anilist, "AniList", "no match found", ok=False)
            return

        filled = []
        if not self.field_summary.toPlainText().strip() and data.get("description"):
            self.field_summary.setPlainText(_strip_html(data["description"]))
            filled.append("summary")
        if not self.field_genre.text().strip() and data.get("genres"):
            self.field_genre.setText(", ".join(data["genres"]))
            filled.append("genre")

        staff_lines = [
            f"{role}: {', '.join(names)}" for role, names in data.get("staff", {}).items()
        ]
        self.lookup_staff_output.setPlainText("\n".join(staff_lines))

        suffix = f" - filled {', '.join(filled)}" if filled else " - summary/genre already set, left as-is"
        self._set_lookup_status(
            self.lookup_status_anilist, "AniList", f"found {data['title']!r}{suffix}", ok=True
        )

    # ---- series profile ---------------------------------------------------

    def _load_profile_clicked(self) -> None:
        series_name = self.field_series.text().strip()
        if not series_name:
            QMessageBox.information(self, "Load profile", "Enter a series name first.")
            return
        profile = load_or_create(self.profiles_dir, series_name)
        self.field_writer.setText(profile.writer)
        self.field_penciller.setText(profile.penciller)
        self.field_inker.setText(profile.inker)
        self.field_cover_artist.setText(profile.cover_artist)
        self.field_publisher.setText(profile.publisher)
        self.field_imprint.setText(profile.imprint)
        self.field_genre.setText(profile.genre)
        self.field_language_iso.setText(profile.language_iso)
        self.field_age_rating.setText(profile.age_rating)
        self.field_black_and_white.setCurrentText(profile.black_and_white)
        self.field_manga.setCurrentText(profile.manga)
        if self.field_volume.value():
            self.field_title.setText(profile.format_title(self.field_volume.value()))
        self.statusBar().showMessage(f"Loaded series profile for {series_name}")

    def _save_profile_clicked(self) -> None:
        series_name = self.field_series.text().strip()
        if not series_name:
            QMessageBox.information(self, "Save profile", "Enter a series name first.")
            return
        profile = SeriesProfile(
            name=series_name,
            writer=self.field_writer.text(),
            penciller=self.field_penciller.text(),
            inker=self.field_inker.text(),
            cover_artist=self.field_cover_artist.text(),
            publisher=self.field_publisher.text(),
            imprint=self.field_imprint.text(),
            genre=self.field_genre.text(),
            language_iso=self.field_language_iso.text(),
            age_rating=self.field_age_rating.text(),
            black_and_white=self.field_black_and_white.currentText(),
            manga=self.field_manga.currentText(),
        )
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        profile.save(profile_path(self.profiles_dir, series_name))
        self.statusBar().showMessage(f"Saved series profile for {series_name}")

    # ---- build ---------------------------------------------------------

    def _gather_metadata(self) -> SeriesMetadata:
        return SeriesMetadata(
            series=self.field_series.text(),
            volume=self.field_volume.value(),
            number=self.field_number.text(),
            title=self.field_title.text(),
            summary=self.field_summary.toPlainText(),
            notes=self.field_notes.toPlainText(),
            year=self.field_year.value() or None,
            month=self.field_month.value() or None,
            day=self.field_day.value() or None,
            writer=self.field_writer.text(),
            penciller=self.field_penciller.text(),
            inker=self.field_inker.text(),
            cover_artist=self.field_cover_artist.text(),
            publisher=self.field_publisher.text(),
            imprint=self.field_imprint.text(),
            genre=self.field_genre.text(),
            language_iso=self.field_language_iso.text(),
            age_rating=self.field_age_rating.text(),
            black_and_white=self.field_black_and_white.currentText(),
            manga=self.field_manga.currentText(),
        )

    def _build_clicked(self) -> None:
        if self.manifest is None:
            QMessageBox.information(self, "Build", "Open a source first.")
            return

        meta = self._gather_metadata()
        try:
            xml_text = build_comicinfo_xml(self.manifest, meta)
        except ValueError as exc:
            QMessageBox.critical(self, "Cannot build", str(exc))
            return

        expected_page_count = len(resolve_output_pages(self.manifest))
        errors = validate_comicinfo_xml(xml_text, expected_page_count)
        if errors:
            QMessageBox.critical(self, "Validation failed", "\n".join(errors))
            return

        default_name = f"{meta.series} Vol. {meta.volume} - {meta.writer}.cbz"
        output_path, _ = QFileDialog.getSaveFileName(
            self, "Build CBZ", default_name, "CBZ (*.cbz)"
        )
        if not output_path:
            return

        try:
            build_cbz(self.manifest, xml_text, output_path)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            QMessageBox.critical(self, "Build failed", str(exc))
            return

        QMessageBox.information(self, "Build complete", f"Wrote {output_path}")
        self.statusBar().showMessage(f"Built {output_path}")
