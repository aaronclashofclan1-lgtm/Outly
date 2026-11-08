# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for Outly: one windowed executable."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = Path(SPECPATH).resolve()

datas = [
    (str(ROOT / "outly" / "schema.sql"), "outly"),
    (str(ROOT / "outly" / "gui" / "assets"), "outly/gui/assets"),
    (str(ROOT / "outly" / "schemas"), "outly/schemas"),
    (str(ROOT / "outly" / "modeles"), "outly/modeles"),
    (str(ROOT / "guides"), "guides"),
]
binaries = []
hiddenimports = [
    "outly.gui.app",
    "outly.gui.recherche_page",
    "outly.gui.studio_page",
    "outly.gui.strategie_page",
    "outly.cli",
    "PIL.Image",
    "PIL.ImageTk",
    "youtube_transcript_api",
]

# CustomTkinter loads themes and fonts from its package data.
ctk_data, ctk_binaries, ctk_imports = collect_all("customtkinter")
datas += ctk_data
binaries += ctk_binaries
hiddenimports += ctk_imports

# Windows does not ship the IANA timezone database.
datas += collect_data_files("tzdata")

for name in ("logo.png", "logo.ico"):
    source = ROOT / "outly" / "gui" / name
    if source.is_file():
        datas.append((str(source), "outly/gui"))

icon = ROOT / "build" / "outly.ico"
version = ROOT / "build" / "version-outly.txt"

a = Analysis(
    [str(ROOT / "lanceur_exe.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Outly",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(icon) if icon.is_file() else None,
    version=str(version) if version.is_file() else None,
)
