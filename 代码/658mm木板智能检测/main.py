import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from boardcapture.config import ROOT, ConfigStore

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--vision-worker")
    parser.add_argument("--grading-worker")
    parser.add_argument("--processing-worker")
    parser.add_argument("--import-settings",action="store_true")
    parser.add_argument("--diagnose",action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--screenshot")
    parser.add_argument("--smoke-test",action="store_true")
    parser.add_argument("--simulate-test",action="store_true")
    parser.add_argument("--recover")
    parser.add_argument("--recover-dir")
    parser.add_argument("--recover-format",choices=["BMP","JPG","PNG","TIFF"],default="BMP")
    args=parser.parse_args()
    if args.processing_worker:
        from boardcapture.processing import worker
        return worker(args.processing_worker)
    if args.grading_worker:
        from boardcapture.grading import run_worker
        return run_worker(args.grading_worker)
    if args.vision_worker:
        from boardcapture.vision_jobs import worker
        return worker(args.vision_worker)
    if args.simulate_test:
        from boardcapture.diagnostics import simulation_acceptance
        return simulation_acceptance()
    if args.recover:
        from boardcapture.recovery import recover
        from boardcapture.config import atomic_json
        report=ROOT/"data"/"recovery_result.json"
        try:
            if not args.recover_dir:
                raise ValueError("恢复时必须指定 --recover-dir")
            path=recover(args.recover,args.recover_dir,args.recover_format)
            atomic_json(report,{"ok":True,"path":str(path)})
            if sys.stdout: print(path)
            return 0
        except Exception as e:
            atomic_json(report,{"ok":False,"error":str(e)})
            if sys.stderr: print(str(e),file=sys.stderr)
            return 1
    if args.self_test or args.diagnose:
        from boardcapture.diagnostics import diagnose
        return diagnose(args.config, enumerate_hardware=args.diagnose)
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    app=QApplication(sys.argv)
    app.setApplicationName("658mm木板智能检测")
    (ROOT/"data"/"logs").mkdir(parents=True,exist_ok=True)
    lock=QLockFile(str(ROOT/"data"/"application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.warning(None,"程序已运行","本目录的采集程序已经运行，请使用已有窗口。")
        return 1
    if args.import_settings:
        from PySide6.QtWidgets import QFileDialog
        from boardcapture.import_settings import import_settings
        path,_=QFileDialog.getOpenFileName(None,'选择同款相机旧版 config/settings.json','','现场设置 (*.json)')
        if path:
            try:
                target=import_settings(path)
                QMessageBox.information(None,'现场设置已导入','PLC、相机参数与已应用标定已导入。\n请确认新版本的图片保存目录和下一编号。\n'+str(target))
            except Exception as e:
                QMessageBox.critical(None,'导入失败',str(e));lock.unlock();return 1
        lock.unlock();return 0
    handler=RotatingFileHandler(ROOT/"data"/"logs"/"capture.log",maxBytes=5*1024*1024,backupCount=8,encoding="utf-8")
    logging.basicConfig(level=logging.INFO,handlers=[handler],format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
    try:
        store=ConfigStore(args.config)
    except Exception as e:
        logging.exception("配置加载失败")
        QMessageBox.critical(None,"配置错误",str(e))
        return 1
    from boardcapture.gui import Window
    window=Window(store,start_engine=not (args.screenshot or args.smoke_test))
    window.show()
    if args.screenshot:
        def screenshot():
            window.grab().save(str(Path(args.screenshot).resolve()))
            window.close()
        QTimer.singleShot(600,screenshot)
    elif args.smoke_test:
        QTimer.singleShot(600,window.close)
    result=app.exec()
    lock.unlock()
    return result

if __name__=="__main__":
    raise SystemExit(main())
