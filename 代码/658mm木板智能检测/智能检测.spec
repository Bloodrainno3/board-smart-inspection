from pathlib import Path
project = Path(SPECPATH)
a = Analysis(
    [str(project/'main.py')], pathex=[str(project)],
    binaries=[(str(project/'camera_driver'/name),'camera_driver') for name in
              ('board_camera_bridge.dll','IKapBoard.dll','dvp.dll','VCOMP140.DLL','MSVCR100.dll',
               'cl_config_bridge.dll','lsn_cl_configurator.dll')],
    datas=[(str(project/'models'),'models')],
    hiddenimports=[], hookspath=[], runtime_hooks=[],
    excludes=['pythonnet','clr','pytest','pypdf','torch','torchvision','segmentation_models_pytorch','timm','onnx','PySide6.QtQml','PySide6.QtQuick','PySide6.QtWebEngineCore'],
    noarchive=False,
)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='658mm木板智能检测',debug=False,
        bootloader_ignore_signals=False,strip=False,upx=False,console=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='658mm木板智能检测')
