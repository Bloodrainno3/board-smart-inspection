"""Captured-board processing isolated from PLC polling and camera I/O."""
from __future__ import annotations
import copy,json,os,queue,subprocess,sys,threading,time,uuid
from pathlib import Path
from datetime import datetime
from .config import ROOT,atomic_json
from .result_stream import Journal,TcpServer,now

class Processing:
    def __init__(self,store,data_root,emit):
        self.store=store;self.root=Path(data_root)/'online';self.emit=emit
        self.incoming=queue.Queue();self.stopped=threading.Event();self.thread=None
        self.journal=None;self.server=None;self.server_key=None;self.last_retry=0
        self.process=None;self.active=None;self.worker_log=None;self.reported=set();self.last_state=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.thread=threading.Thread(target=self.run,daemon=True,name='OnlineProcessing');self.thread.start()
    def enqueue(self,path,settings,simulation=False):
        self.incoming.put(('image',str(Path(path).resolve()),copy.deepcopy(settings),bool(simulation),now(),uuid.uuid4().hex))
    def resume(self):self.incoming.put(('resume',))
    def _event(self,req,kind,**data):
        event=self.journal.publish(req['board_id']+'/'+kind,dict(type=kind,board_id=req['board_id'],
            board_number=Path(req['source']).stem,image_path=req['source'],captured_at=req['captured_at'],
            simulation=req['simulation'],enabled_modules=req['modules'],**data))
        self.emit('processing_event',event)
        return event
    def _accept(self,item):
        _,source,cfg,simulation,saved_at,board_id=item
        stat=Path(source).stat();p=cfg['processing']
        modules=[]
        if cfg['vision']['enabled']:modules.append('seam')
        if p['auto_grade']:modules.append('grading')
        req={'board_id':board_id,'source':source,'modules':modules,'simulation':simulation,
             'captured_at':saved_at,'source_size':stat.st_size,'source_mtime_ns':stat.st_mtime_ns,
             'vision':cfg['vision'],'processing':p,'out':str(Path(p['results_directory'])/board_id)}
        directory=Path(req['out']);atomic_json(directory/'request.json',req)
        self.journal.add_job(req);self._event(req,'board.saved')
    def _ensure_server(self):
        p=self.store.snapshot()['processing'];key=(p['tcp_enabled'],p['tcp_host'],p['tcp_port'],p['tcp_token'])
        if key==self.server_key and (self.server or not key[0] or time.monotonic()-self.last_retry<10):return
        if self.server:self.server.stop();self.server=None
        self.server_key=key;self.last_retry=time.monotonic()
        if key[0]:
            try:
                self.server=TcpServer(self.journal,key[1],key[2],key[3])
                self.emit('processing_tcp',{'listening':True,'host':key[1],'port':key[2]})
            except OSError as e:self.emit('processing_tcp',{'listening':False,'error':str(e)})
        else:self.emit('processing_tcp',{'listening':False})
    def _launch(self,req):
        self.active=req;self.reported=set();self.journal.mark(req['board_id'],'running')
        self._event(req,'board.saved') # Idempotent, including recovery after an interrupted enqueue.
        if not req['modules']:self._complete(req,{});return
        out=Path(req['out']);self.worker_log=(out/'worker.log').open('ab')
        atomic_json(out/'request.json',req)
        prefix=[sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(ROOT/'main.py')]
        flags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name=='nt' else 0
        self.process=subprocess.Popen(prefix+['--processing-worker',str(out/'request.json')],
            stdout=self.worker_log,stderr=self.worker_log,stdin=subprocess.DEVNULL,creationflags=flags,cwd=str(ROOT))
    def _read_module(self,req,module):
        try:return json.loads((Path(req['out'])/(module+'.json')).read_text(encoding='utf-8'))
        except (OSError,ValueError):return None
    def _poll_active(self):
        req=self.active
        if not req or not self.process:return
        modules={m:self._read_module(req,m) for m in req['modules']}
        for module,result in modules.items():
            if result and module not in self.reported:
                event=self._event(req,module+'.'+('completed' if result['status']=='done' else 'failed'),result=result)
                self.reported.add(module)
                if module=='seam' and result['status']=='done':self.emit('vision_result',result['result'])
        code=self.process.poll()
        if code is None:return
        self.worker_log.close();self.worker_log=None;self.process=None
        for module in req['modules']:
            if not modules[module]:
                modules[module]={'status':'error','error':f'识别进程退出({code})，未生成结果，请查看worker.log'}
                self._event(req,module+'.failed',result=modules[module])
        self._complete(req,modules)
    def _complete(self,req,modules):
        reasons=[];seam=modules.get('seam');grade=modules.get('grading')
        if req['simulation']:reasons.append('simulation')
        if req.get('resumed'):reasons.append('resumed_historical_board')
        if not req['modules']:reasons.append('capture_only')
        for m,r in modules.items():
            if r['status']!='done':reasons.append(m+'_failed')
        if seam and seam['status']=='done':
            r=seam['result']
            if r.get('status')!='measured' or r.get('warnings') or not r.get('seams'):
                reasons.append('seam_measurement_needs_review')
            if any(s.get('distance_from_board_top_mm') is None or not s.get('coordinate_valid') or s.get('boundary_pair_completed') for s in r.get('seams',[])):
                reasons.append('seam_coordinate_or_calibration_needs_review')
        if grade and grade['status']=='done' and grade['result']['confidence']<req['processing']['min_confidence']:
            reasons.append('grading_low_confidence')
        # Frozen board coordinates/labels are data, never actuator commands.
        event=self._event(req,'board.completed',results=modules,requires_review=bool(reasons),review_reasons=reasons,
                          result_latency_ms=max(0,round((datetime.fromisoformat(now())-datetime.fromisoformat(req['captured_at'])).total_seconds()*1000)),
                          status='error' if any(r['status']!='done' for r in modules.values()) else 'complete')
        atomic_json(Path(req['out'])/'board_result.json',event)
        self.journal.mark(req['board_id'],'done');self.active=None
    def _state(self):
        state=self.journal.counts()
        cfg=self.store.snapshot()
        state['mode']=[cfg['vision']['enabled'],cfg['processing']['auto_grade']]
        state['material']=cfg['processing']['material']
        state['tcp_clients']=len(self.server.clients) if self.server else 0
        state['latest_seq']=self.journal.latest()
        if state!=self.last_state:self.emit('processing_state',state);self.last_state=state
    def run(self):
        try:
            while not self.stopped.is_set():
                try:
                    if self.journal is None:
                        journal=Journal(self.root/'events.sqlite');journal.hold_interrupted();self.journal=journal
                    for _ in range(32):
                        try:item=self.incoming.get_nowait()
                        except queue.Empty:break
                        if item[0]=='resume':self.journal.resume()
                        else:
                            try:self._accept(item)
                            except Exception:
                                self.incoming.put(item);raise
                    self._ensure_server();self._poll_active()
                    if not self.active:
                        pending=self.journal.jobs('queued')
                        if pending:self._launch(pending[0])
                    self._state()
                except Exception as e:
                    self.emit('processing_error','在线处理：'+str(e))
                    if self.active and self.process is None:
                        if self.worker_log:self.worker_log.close();self.worker_log=None
                        self.journal.mark(self.active['board_id'],'held');self.active=None
                    self.stopped.wait(.5)
                self.stopped.wait(.1)
        finally:
            if self.process:
                self.process.terminate()
                try:self.process.wait(5)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(2)
            if self.worker_log:self.worker_log.close()
            # The engine calls close only after all image saves have finished.
            while True:
                try:item=self.incoming.get_nowait()
                except queue.Empty:break
                try:
                    if item[0]=='image':self._accept(item)
                except Exception as e:self.emit('processing_error','关闭时任务记录失败：'+str(e))
            if self.journal:self.journal.hold_interrupted()
            if self.server:self.server.stop()
    def close(self):
        self.stopped.set()
        if self.thread:self.thread.join(10)

def worker(request_path):
    """Two enabled analyses may execute together, in a lower-priority process."""
    from concurrent.futures import ThreadPoolExecutor
    request_path=Path(request_path);req=json.loads(request_path.read_text(encoding='utf-8'));out=request_path.parent
    def analyze(module):
        try:
            stat=Path(req['source']).stat()
            if (stat.st_size,stat.st_mtime_ns)!=(req['source_size'],req['source_mtime_ns']):
                raise ValueError('原图已改变，不能把旧结果关联给该板')
            existing=out/(module+'.json')
            if existing.exists():
                prior=json.loads(existing.read_text(encoding='utf-8'))
                if prior.get('status')=='done':return
            if module=='seam':
                import cv2
                cv2.setNumThreads(1)
                from .vision import Predictor
                cfg=req['vision'];predictor=Predictor(cfg['model_path'],cfg.get('threads',2))
                result=predictor.analyze(req['source'],out/'seam',cfg,cfg.get('calibration'))
            else:
                from .grading import Predictor
                cfg=req['processing'];result=Predictor(cfg['material'],cfg['grading_threads']).predict(req['source'])
            stat=Path(req['source']).stat()
            if (stat.st_size,stat.st_mtime_ns)!=(req['source_size'],req['source_mtime_ns']):raise ValueError('处理期间原图发生变化')
            payload={'status':'done','result':result}
        except Exception as e:
            import traceback
            payload={'status':'error','error':str(e),'traceback':traceback.format_exc()}
        atomic_json(out/(module+'.json'),payload)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(analyze,req['modules']))
    return 0
