# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 模式）。

选 onedir 而不是 onefile 的原因：
  1. onedir 不需要在运行时把自身解压到 %TEMP%，规避杀软误报与受限 TEMP 环境；
  2. 启动明显更快（免解压）；
  3. 单目录分发对普通用户仍是「双击 SaveKeeper.exe 即用」，
     再用 build_release.py 打成 zip 即可，无需安装。

用法：
    python -m PyInstaller SaveKeeper.spec --noconfirm --clean
产物：
    dist/SaveKeeper/SaveKeeper.exe
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("tkinter") + [
    "savekeeper",
    "savekeeper.cli",
    "savekeeper.core",
    "savekeeper.config",
    "savekeeper.games",
    "savekeeper.gui",
    "savekeeper.paths",
]

a = Analysis(
    ["run_gui.py"],
    pathex=["."],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "PIL",
        "numpy",
        "setuptools",
        "pip",
        "pydoc",
        "doctest",
        "unittest",
        "test",
        "lib2to3",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SaveKeeper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # GUI 程序，不弹黑框
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SaveKeeper",
)
