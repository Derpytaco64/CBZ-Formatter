import os

# Must be set before any PySide6/Qt import happens, so GUI tests can run
# in this headless environment (no X/Wayland display).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
