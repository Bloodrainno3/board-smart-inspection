"""Durable, serial inference queue. Heavy libraries load only in a child process."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from .config import ROOT, ASSETS, atomic_json


def vision_defaults():
    return {'enabled':False,'manual_window_v1':True,'model_path':str(ASSETS/'models'/'best.onnx'),
            'results_directory':str(ROOT/'results'),'threshold':.5,'max_seams':2,
            'min_row_coverage':.08,'threads':2,'material':'混合板材','calibration':None}


class VisionQueue:
    def __init__(self, data_root, emit):
        self.root=Path(data_root)/'vision_queue'
        self.emit=emit
        self.stop_event=threading.Event()
        self.thread=None
        self.process=None
        self.lock=threading.RLock()
        self.last_state=None

    def start(self):
        with self.lock:
            self._start_locked()

    def _start_locked(self):
        if self.thread and self.thread.is_alive():return
        self.root.mkdir(parents=True,exist_ok=True)
        # A stopped/crashed inference can be recomputed from the saved original.
        for p in self.root.glob('*/state.json'):
            try:
                s=json.loads(p.read_text(encoding='utf-8'))
                if s['status'] in ('running','queued'):
                    atomic_json(p,{'status':'held','reason':'重启后等待用户点击继续未完成任务'})
            except (OSError,ValueError,KeyError):pass
        self.thread=threading.Thread(target=self.run,name='VisionQueue',daemon=True)
        self.thread.start()

    def enqueue(self,path,settings):
        self.start()
        settings=copy.deepcopy(settings)
        jobid=time.strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:12]
        job=self.root/jobid
        out=Path(settings['results_directory'])/jobid
        request={'kind':'analyze','source':str(Path(path).resolve()),'out':str(out.resolve()),'settings':settings}
        atomic_json(job/'request.json',request)
        atomic_json(job/'state.json',{'status':'queued'})
        self.emit('vision_notice','已加入识别队列：'+Path(path).name)
        return jobid

    def calibration_job(self,path,square_mm,paper_width_mm=210,paper_height_mm=297):
        self.start()
        job=self.root/(time.strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:12])
        request={'kind':'calibrate','source':str(Path(path).resolve()),'out':str(job/'calibration'),
                 'square_mm':square_mm,'paper_width_mm':paper_width_mm,'paper_height_mm':paper_height_mm}
        atomic_json(job/'request.json',request)
        atomic_json(job/'state.json',{'status':'queued'})

    def snapshot(self):
        jobs=[];counts={'queued':0,'running':0,'error':0,'done':0,'held':0}
        for p in sorted(self.root.glob('*/state.json')):
            try:
                state=json.loads(p.read_text(encoding='utf-8'))
                status=state.get('status','error');counts[status]=counts.get(status,0)+1
                if status=='queued':jobs.append(p.parent)
            except (OSError,ValueError):continue
        return jobs,counts

    def retry_errors(self):
        self.start()
        for p in self.root.glob('*/state.json'):
            try:
                if json.loads(p.read_text(encoding='utf-8')).get('status') in ('error','held'):atomic_json(p,{'status':'queued'})
            except (OSError,ValueError):continue

    def run(self):
        while not self.stop_event.is_set():
            try:
                jobs,counts=self.snapshot()
                if counts!=self.last_state:self.emit('vision_state',counts);self.last_state=counts
                if not jobs:
                    self.stop_event.wait(.3);continue
                job=jobs[0]
                request=json.loads((job/'request.json').read_text(encoding='utf-8'))
                atomic_json(job/'state.json',{'status':'running'})
                counts['queued']-=1;counts['running']+=1;self.emit('vision_state',counts)
                prefix=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(ROOT/'main.py')]
                flags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name=='nt' else 0
                with (job/'worker.log').open('wb') as log:
                    self.process=subprocess.Popen(prefix+['--vision-worker',str(job/'request.json')],stdout=log,stderr=log,
                        stdin=subprocess.DEVNULL,creationflags=flags,cwd=str(ROOT))
                    last_progress=None
                    while self.process.poll() is None:
                        if self.stop_event.wait(.25):
                            self.process.terminate();self.process.wait(timeout=5)
                            atomic_json(job/'state.json',{'status':'queued','reason':'关闭后等待下次恢复'})
                            return
                        try:
                            p=json.loads((job/'progress.json').read_text(encoding='utf-8'))
                            if p!=last_progress:self.emit('vision_progress',p);last_progress=p
                        except (OSError,ValueError):pass
                    code=self.process.returncode;self.process=None
                reply_path=job/'reply.json'
                if code!=0 or not reply_path.exists():
                    reason='识别子进程退出，详情见 '+str(job/'worker.log')
                    if reply_path.exists():reason=json.loads(reply_path.read_text(encoding='utf-8')).get('error',reason)
                    atomic_json(job/'state.json',{'status':'error','error':reason})
                    self.emit('vision_error',reason)
                else:
                    reply=json.loads(reply_path.read_text(encoding='utf-8'))
                    atomic_json(job/'state.json',{'status':'done','reply':str(reply_path)})
                    self.emit('calibration_result' if request['kind']=='calibrate' else 'vision_result',reply)
            except Exception as e:
                self.emit('vision_error',str(e));self.stop_event.wait(1)

    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(timeout=8)


def worker(request_path):
    # Windows releases this byte lock if a worker crashes. After a forced parent
    # exit an older worker may still finish; a restarted queue must not overwrite
    # that same job's partial outputs concurrently.
    job=Path(request_path).parent
    with (job/'worker.lock').open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        if os.name=='nt':
            import msvcrt
            while True:
                handle.seek(0)
                try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:time.sleep(.2)
        try:
            try:
                reply=json.loads((job/'reply.json').read_text(encoding='utf-8'))
                if 'error' not in reply:
                    complete=reply.get('result_file') or reply.get('preview')
                    if complete and Path(complete).is_file():return 0
            except (OSError,ValueError):pass
            return _run_worker(request_path)
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


def _run_worker(request_path):
    request_path=Path(request_path);job=request_path.parent
    try:
        req=json.loads(request_path.read_text(encoding='utf-8'))
        import cv2
        cv2.setNumThreads(1)
        if req['kind']=='calibrate':
            from .geometry import calibrate
            import numpy as np
            profile,image,meta=calibrate(req['source'],req['square_mm'],req['paper_width_mm'],req['paper_height_mm'])
            out=Path(req['out']);out.mkdir(parents=True,exist_ok=True)
            for pts,color in ((profile['paper_corners_px'],(0,0,255)),(profile['checker_corners_px'],(0,255,0))):
                for x,y in pts:cv2.circle(image,(round(x/meta['scale_x']),round(y/meta['scale_y'])),5,color,-1)
            # Crop around paper for useful preview.
            ys=np.array(profile['paper_corners_px'])[:,1]/meta['scale_y']
            cv2.imencode('.jpg',image[max(0,int(ys.min())-40):int(ys.max())+40])[1].tofile(str(out/'calibration.jpg'))
            profile['preview']=str(out/'calibration.jpg')
            atomic_json(out/'calibration.json',profile)
            reply=profile
        else:
            from .vision import Predictor
            settings=req['settings'];predictor=Predictor(settings['model_path'],settings.get('threads',2))
            def progress(i,n):atomic_json(job/'progress.json',{'file':req['source'],'tile':i,'total':n})
            reply=predictor.analyze(req['source'],req['out'],settings,settings.get('calibration'),progress)
        atomic_json(job/'reply.json',reply)
        return 0
    except Exception as e:
        import traceback
        atomic_json(job/'reply.json',{'error':str(e),'traceback':traceback.format_exc()})
        if sys.stderr:traceback.print_exc()
        return 1
