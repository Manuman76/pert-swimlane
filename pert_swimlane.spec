# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the PERT Swimlane desktop app (Windows)."""

from PyInstaller.utils.hooks import collect_all

datas = [
    ("app/templates", "app/templates"),
    ("app/static", "app/static"),
]
binaries = []
hiddenimports = []

for pkg in ("webview", "clr_loader", "pythonnet"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    ["desktop.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports
    + [
        "flask",
        "flask_sqlalchemy",
        "sqlalchemy",
        "jinja2",
        "werkzeug",
    ],
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
    name="PERTSwimlane",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="PERTSwimlane",
)
