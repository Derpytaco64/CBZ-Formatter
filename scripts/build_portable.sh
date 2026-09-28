#!/usr/bin/env bash
# Build a portable, self-contained cbzforge bundle (onedir) for the current
# platform. PyInstaller does not cross-compile - run this script on each
# target OS (Linux/Windows/macOS) to produce that platform's bundle.
#
# Output: dist/cbzforge/ - copy the whole folder to move it. No Python
# install, venv, or system tesseract needed on the target machine.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
    echo "no .venv found - run: python3 -m venv .venv && .venv/bin/pip install -e '.[build]'" >&2
    exit 1
fi

# Built from cbzforge.spec (not raw CLI flags) so manual customizations in
# it - e.g. the app icon - survive rebuilds instead of being regenerated
# away every time.
.venv/bin/pyinstaller --noconfirm --clean cbzforge.spec

echo
echo "Built dist/cbzforge/ - verifying with --selftest..."
QT_QPA_PLATFORM=offscreen dist/cbzforge/cbzforge --selftest
