# -*- mode: python ; coding: utf-8 -*-
# Build from the repo root:  python -m PyInstaller packaging/SortJunk.spec --noconfirm
# Paths are anchored to this file (SPECPATH), so the build works from any cwd;
# output still lands in ./dist and ./build of wherever it's run from.
import os

ROOT = os.path.dirname(SPECPATH)
HERE = SPECPATH

a = Analysis(
    [os.path.join(HERE, 'run_gui.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[],
    datas=[(os.path.join(ROOT, 'src', 'sortjunk', 'assets'), 'sortjunk/assets')],
    hiddenimports=[],
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
    a.binaries,
    a.datas,
    [],
    name='SortJunk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed exes trigger far more antivirus false positives
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=os.path.join(HERE, 'version_info.txt'),
    icon=[os.path.join(HERE, 'SortJunk.ico')],
)
