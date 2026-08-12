# -*- mode: python ; coding: utf-8 -*-
"""
build/dicom_toolkit.spec
==========================
PyInstaller build spec for DicomToolkit.exe.

Run via `pyinstaller build\\dicom_toolkit.spec` from the project root
(build.bat does this for you automatically, after first creating a venv
and installing requirements.txt).

`collect_all` is used for pydicom and pynetdicom because both packages
resolve some of their submodules/data (character set tables, SOP class
definitions, DIMSE status code tables) dynamically at runtime rather
than through plain top-level imports, which PyInstaller's static import
analysis can otherwise miss - without this, the packaged exe can fail
at runtime with "module not found" even though it builds successfully.
"""

import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None
project_root = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = []
binaries = []
hiddenimports = []
for pkg in ("pydicom", "pynetdicom"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    [os.path.join(project_root, "main.py")],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="DicomToolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # console=False -> no terminal window pops up alongside the GUI.
    # Flip to True temporarily if you need to see Python tracebacks while
    # debugging a build (see README "Troubleshooting the build").
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
