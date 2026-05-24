# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# 添加资源文件
datas = [
    ('favicon.ico', '.'),
    ('brightness_model.npz', '.'),
    ('config.json', '.'),
    ('liangdu.py', '.'),
    ('config.py', '.'),
    ('notification.py', '.'),
    ('utils.py', '.'),
]

# 配置
a = Analysis(
    ['app.py'],
    pathex=[r'd:\niwei\Documents\trae_projects\light\source'],
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
        'threading',
        'time',
        'ctypes',
        'os',
        'sys',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 创建exe配置
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BrightnessManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 设置为False，实现无界面运行
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='favicon.ico'  # 设置应用程序图标
)

# 收集文件
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='BrightnessManager'
)

