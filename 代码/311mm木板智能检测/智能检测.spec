# Reproducible onedir build. Test SDK and pythonnet are deliberately excluded.
from pathlib import Path
project = Path(SPECPATH)
a = Analysis(
    [str(project / 'main.py')], pathex=[str(project)],
    binaries=[(str(project/'camera_driver'/'board_camera_bridge.dll'),'camera_driver'),
              (str(project/'camera_driver'/'lsn_gv_camera.dll'),'camera_driver')],
    datas=[(str(project/'calibration'/'default.calib'),'calibration'),(str(project/'models'),'models')],
    hiddenimports=[], hookspath=[], runtime_hooks=[],
    excludes=['pythonnet','clr','pytest','pypdf','torch','torchvision','segmentation_models_pytorch','timm','onnx','PySide6.QtQml','PySide6.QtQuick','PySide6.QtWebEngineCore'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='311mm木板智能检测',
          debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='311mm木板智能检测')
