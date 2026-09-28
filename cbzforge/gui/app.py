from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from cbzforge.gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    icon_path = Path(__file__).parent / "resources" / "app_icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.resize(1300, 850)

    if "--selftest" in sys.argv:
        # Used to verify a frozen/portable build actually launches (imports
        # resolve, models load) without needing a real display - construct
        # everything, then exit immediately instead of starting the event loop.
        from cbzforge import ocr

        ocr_ok = ocr.is_available()
        print(f"selftest OK (bundled OCR available: {ocr_ok})")
        return 0 if ocr_ok else 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
