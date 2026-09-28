# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all

_icon_path = Path('cbzforge/gui/resources/app_icon.ico')
icon = str(_icon_path) if _icon_path.exists() else None

datas = []
binaries = []
hiddenimports = []
datas += collect_data_files('cbzforge')
# collect_data_files() resolves the package via importlib.metadata, whose
# editable-install RECORD is a static snapshot from `pip install -e .` time
# - it won't see files (like the generated app icon) added afterward, so
# the resources directory is added explicitly rather than relying on it.
_resources_dir = Path('cbzforge/gui/resources')
if _resources_dir.is_dir():
    for f in _resources_dir.iterdir():
        if f.is_file():
            datas.append((str(f), str(_resources_dir)))
tmp_ret = collect_all('rapidocr_onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['cbzforge/gui/app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='cbzforge',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='cbzforge',
)
