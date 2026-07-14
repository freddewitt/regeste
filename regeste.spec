# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Regeste — single onefile executable serving both the GUI
(default) and the CLI (`--cli`), exactly like `regeste/__main__.py` does at runtime.

Rebuild on each target OS (PyInstaller does not cross-compile):
    pip install -e ".[build]"
    pyinstaller regeste.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

block_cipher = None

# --- Data files ---------------------------------------------------------
# Translation catalogs (compiled .mo only — .po/.pot are dev-time artifacts,
# not needed at runtime) for all 9 interface languages. Without these the
# packaged executable would silently fall back to English/NullTranslations.
# Each (source, dest) pair below must preserve the `<lang>/LC_MESSAGES/`
# subdirectory structure gettext relies on to pick a catalog by locale code —
# a single flattened destination breaks language detection entirely.
_LOCALE_ROOT = Path("regeste") / "locale"
datas = [
    (str(mo_path), str(mo_path.parent))
    for mo_path in sorted(_LOCALE_ROOT.glob("*/LC_MESSAGES/*.mo"))
]

# reportlab ships its own font/data resources (AFM metrics, etc.) needed to
# generate searchable PDFs.
datas += collect_data_files("reportlab")

# --- Native libraries -----------------------------------------------------
# pillow-heif bundles its own libheif/libde265/libx265 dynamic libraries as
# a Pillow plugin registered at import time; PyInstaller's static analysis
# does not follow that registration, so both the binaries and the hidden
# import must be declared explicitly.
binaries = collect_dynamic_libs("pillow_heif")

# --- Hidden imports ---------------------------------------------------
# Provider SDKs are imported lazily/conditionally inside core/providers/*,
# so PyInstaller's static import scan misses submodules loaded dynamically
# by these packages (gRPC/protobuf plumbing in google-genai, pydantic model
# builders in openai/anthropic, etc.).
hiddenimports = [
    "pillow_heif",
]
hiddenimports += collect_submodules("google.genai")
hiddenimports += collect_submodules("google.auth")
hiddenimports += collect_submodules("anthropic")
hiddenimports += collect_submodules("openai")
hiddenimports += collect_submodules("pydantic")
hiddenimports += collect_submodules("cv2")

a = Analysis(
    ["regeste/__main__.py"],
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
    a.binaries,
    a.datas,
    [],
    name="regeste",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # Kept True: `regeste --cli` needs a real stdin/stdout console. GUI mode
    # (default, no --cli) still works fine when launched from a terminal;
    # on macOS/Windows this means double-clicking opens a console window
    # alongside the GUI, a known and accepted tradeoff for a single
    # GUI+CLI onefile binary.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
