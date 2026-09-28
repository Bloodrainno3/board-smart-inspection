from datetime import datetime
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import tempfile
from serial.tools.list_ports import comports
from .config import ROOT, ASSETS, ConfigStore, defaults, atomic_json
from .camera import NativeCamera
from .plc import crc16

def diagnose(config_path=None, enumerate_hardware=False):
    (ROOT/"data").mkdir(parents=True,exist_ok=True)
    report={"time":datetime.now().astimezone().isoformat(),"python":sys.version,
        "platform":platform.platform(),"executable":sys.executable,"root":str(ROOT),
        "pythonnet_required":False,"checks":{},"hardware_tested":False}
    ok=True
    try:
        store=ConfigStore(config_path)
        report["checks"]["configuration"]={"ok":True,"path":str(store.path),"serial":store.snapshot()["serial"],"devices":store.snapshot()["devices"],"repairs":store.notices}
    except Exception as e:
        report["checks"]["configuration"]={"ok":False,"error":str(e)}
        ok=False
    report["checks"]["modbus_crc"]={"ok":crc16(bytes.fromhex("01030000000a"))==0xCDC5}
    try:
        from PIL import Image
        with tempfile.TemporaryDirectory(dir=ROOT/"data") as folder:
            path=Path(folder)/"1.jpg"
            Image.new("RGB",(32,64),(180,100,60)).save(path,format="JPEG")
            with Image.open(path) as img:
                assert img.size==(32,64)
                img.verify()
            assert [p.name for p in Path(folder).iterdir()]==["1.jpg"]
        report["checks"]["jpeg_only"]={"ok":True}
    except Exception as e:
        report["checks"]["jpeg_only"]={"ok":False,"error":str(e)}
        ok=False
    try:
        camera=NativeCamera()
        report["checks"]["native_sdk"]={"ok":True,"abi":camera.dll.bc_abi(),"sdk":"IKapLibrary 1.7.3","interface":"Camera Link / PCIe","pythonnet_required":False}
        if enumerate_hardware:
            devices=camera.enumerate()
            report["camera_devices"]=[s for _,s in devices]
            report["camera_enumeration_message"]="" if devices else camera.error()
        camera.close()
    except Exception as e:
        report["checks"]["native_sdk"]={"ok":False,"error":str(e)}
        ok=False
    try:
        from .cl_config import SDK
        control = SDK()
        report['checks']['cl_config_sdk'] = {'ok': True, 'abi': control.dll.cc_abi(),
            'version': '2026.07.25', 'pythonnet_required': False, 'camera_parameters_read': False}
        if enumerate_hardware:
            report['cl_control_ports'] = control.ports()
        control.close()
    except Exception as e:
        report['checks']['cl_config_sdk'] = {'ok': False, 'error': str(e)}
        ok = False
    report["serial_ports"]=[{"port":p.device,"description":p.description,"hwid":p.hwid} for p in comports()]
    try:
        from .vision import Predictor
        model=Predictor(ASSETS/'models'/'best.onnx',threads=1)
        report['checks']['vision_model']={'ok':True,'architecture':'UNet++/ResNet34',
            'checkpoint_sha256':model.manifest['checkpoint_sha256'],'providers':model.session.get_providers(),
            'input_shape':model.session.get_inputs()[0].shape,'field_calibration_applied':bool(store.snapshot()['vision'].get('calibration'))}
    except Exception as e:
        report['checks']['vision_model']={'ok':False,'error':str(e)};ok=False
    report["packages"]={}
    for name in ("PySide6","Pillow","pyserial","numpy","opencv-python-headless","onnxruntime"):
        try:
            report["packages"][name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            report["packages"][name]="bundled"
    report["status"]="software_checks_passed" if ok else "software_check_failed"
    report["note"]="软件自检不等于真实PLC/相机采集验收。诊断仅枚举设备，不启动采流、不向PLC写入。"
    dest=ROOT/"data"/("hardware_diagnostic.json" if enumerate_hardware else "self_test_result.json")
    atomic_json(dest,report)
    if sys.stdout:
        print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if ok else 1

def simulation_acceptance():
    import time
    import uuid
    from .engine import Engine
    destination=ROOT/"data"/"simulation_tests"/(datetime.now().strftime("%Y%m%d_%H%M%S_")+uuid.uuid4().hex[:6])
    destination.mkdir(parents=True,exist_ok=True)
    runs=[]
    def wait(predicate):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.01)
        raise TimeoutError("模拟测试等待超时")
    try:
        for mode,start in (("ascending",21),("descending",100)):
            case=destination/mode
            store=ConfigStore(case/"settings.json")
            cfg=store.snapshot()
            cfg["output"].update(directory=str(case/"images"),numbering=mode,next_number=start,minimum_free_gb=0)
            cfg["camera"].update(minimum_height=1,max_capture_bytes=4*1024*1024)
            store.save(cfg)
            events=[]
            engine=Engine(store,lambda k,v:events.append((k,v)),case/"data")
            engine.start()
            try:
                engine.submit("simulation",enabled=True)
                engine.submit("plc_connect")
                engine.submit("camera_connect")
                wait(lambda:engine.camera is not None and engine.plc is not None)
                engine.submit('sim_signal',conveyor_running=False)
                engine.submit("arm")
                wait(lambda:engine.auto and engine.trigger.seen_low and engine.sim_status['conveyor_running'] and engine.plc_pulse is None)
                for i in range(3):
                    engine.submit("sim_signal",capture_active=True,front_sensor=True)
                    wait(lambda:engine.capture is not None)
                    time.sleep(.15)
                    engine.submit('sim_signal',front_sensor=False,rear_sensor=True,rear_sensor_seen=True)
                    time.sleep(.15)
                    engine.submit("sim_signal",capture_active=False,front_sensor=False,rear_sensor_seen=True)
                    engine.submit('sim_signal',rear_sensor=False)
                    wait(lambda:len([e for e in events if e[0]=="saved"])==i+1)
                errors=[value for key,value in events if key=="error"]
                if errors:raise RuntimeError(str(errors))
                step=1 if mode=="ascending" else -1
                expected={f"{start+step*i}.jpg" for i in range(3)}
                actual={p.name for p in (case/"images").iterdir()}
                if expected!=actual:raise AssertionError(f"图像文件不匹配：{actual}")
                if store.snapshot()["output"]["next_number"]!=start+step*3:
                    raise AssertionError("下一编号不正确")
                engine.submit('disarm')
                wait(lambda:not engine.auto and not engine.sim_status['conveyor_running'] and engine.plc_pulse is None)
                runs.append({"mode":mode,"files":sorted(actual),"next_number":store.snapshot()["output"]["next_number"],"only_jpg":True,
                             'm300_started':True,'m400_stopped':True,'command_bits_cleared':not engine.sim_status['conveyor_start_command'] and not engine.sim_status['conveyor_stop_command']})
            finally:
                engine.submit("shutdown")
                engine.join(10)
                if engine.is_alive():raise RuntimeError("模拟控制线程没有正常结束")
        report={"ok":True,"real_hardware":False,"runs":runs,"directory":str(destination)}
    except Exception as e:
        report={"ok":False,"error":str(e),"runs":runs,"directory":str(destination)}
    atomic_json(ROOT/"data"/"simulation_test_result.json",report)
    if sys.stdout:print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["ok"] else 1
