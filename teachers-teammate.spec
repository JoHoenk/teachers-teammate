# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ("teachers_teammate/assets", "teachers_teammate/assets"),
    ("teachers_teammate/infrastructure/providers", "teachers_teammate/infrastructure/providers"),
    ("docs/using_the_app.md", "docs"),
    ("docs/advanced_user_guide.md", "docs"),
    ("docs/index.md", "docs"),
    ("README.md", "."),
]
binaries = []
hiddenimports = [
    "teachers_teammate.infrastructure.providers",
    "teachers_teammate.infrastructure.providers.ollama",
    "teachers_teammate.infrastructure.providers.openai",
    "teachers_teammate.infrastructure.providers.anthropic",
    "teachers_teammate.infrastructure.providers.google",
    "teachers_teammate.infrastructure.providers.mistral",
    "teachers_teammate.infrastructure.providers.cohere",
    "timeit",
    "cProfile",
    "_lsprof",
    "teachers_teammate.gui",
    "cv2",
    "pypdfium2",
    "teachers_teammate.cli",
    "teachers_teammate.infrastructure.correction",
    "teachers_teammate.infrastructure.docx_builder",
    "teachers_teammate.infrastructure.image_preprocessor",
]
tmp_ret = collect_all("pip")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]
tmp_ret = collect_all("PySide6")
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]


a = Analysis(
    ["tools/build/run_gui.py"],
    pathex=["."],
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
    name="teachers-teammate",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["teachers_teammate/assets/teachers_teammate_icon.png"],
)
