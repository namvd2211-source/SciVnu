# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['companion_gui.py'],
    pathex=[],
    binaries=[('bin\\cli-proxy-api.exe', 'bin')],
    datas=[('companion_ui', 'companion_ui'), ('backend_api.py', 'backend_runtime'), ('backend_core.py', 'backend_runtime'), ('release_config.json', '.'), ('CHANGELOG.md', 'release_assets')],
    hiddenimports=['backend_api', 'backend_core', 'local_companion_runtime', 'release_config', 'uvicorn'],
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
    name='ResearchCompanion',
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
    name='ResearchCompanion',
)
