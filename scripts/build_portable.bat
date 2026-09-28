@echo off
REM Build a portable, self-contained cbzforge bundle (onedir) on Windows.
REM Untested in this environment (built/verified on Linux only so far) -
REM mirrors build_portable.sh; report back if this needs adjustment.
cd /d "%~dp0\.."

if not exist .venv (
    echo no .venv found - run: python -m venv .venv ^&^& .venv\Scripts\pip install -e ".[build]"
    exit /b 1
)

REM Built from cbzforge.spec (not raw CLI flags) so manual customizations
REM in it - e.g. the app icon - survive rebuilds instead of being
REM regenerated away every time.
.venv\Scripts\pyinstaller --noconfirm --clean cbzforge.spec

echo.
echo Built dist\cbzforge\ - verifying with --selftest...
dist\cbzforge\cbzforge.exe --selftest
