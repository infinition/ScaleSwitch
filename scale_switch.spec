# -*- mode: python ; coding: utf-8 -*-
# ScaleSwitch — PyInstaller build spec
# Build with: pyinstaller scale_switch.spec

a = Analysis(
    ['scale_switch.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pystray._win32', 'PIL._tkinter_finder'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ScaleSwitch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No console window — tray only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # Uses generated PIL icon
    uac_admin=False,
)
