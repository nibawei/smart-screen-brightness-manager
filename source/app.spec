# -*- mode: python ; coding: utf-8 -*-

# 添加所有必要的资源文件
datas = [
    ('favicon.ico', '.'),
    ('brightness_model.npz', '.'),
    ('config.json', '.'),
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'cv2', 
        'numpy', 
        'PIL', 
        'PIL.Image',
        'PIL.ImageTk',
        'pystray', 
        'win32api', 
        'win32con', 
        'win32event', 
        'win32gui', 
        'winerror', 
        'wmi',
        'tkinter',
        'tkinter.ttk',
        'pythoncom',
        'PyQt5',
        'PyQt5.QtWidgets',
        'PyQt5.QtCore',
        'PyQt5.QtGui',
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
    a.binaries,
    a.datas,
    [],
    name='app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['favicon.ico'],
)
