"""Single owner for PLC/camera state; encoding and archival use separate workers."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections import deque
from datetime import datetime
import queue
import threading
import time
from .camera import NativeCamera, SimCamera, RawCapture
from .config import ROOT, MAPPING, atomic_json
from .plc import PLC
from .plc_control import CommandPulse, START, STOP
from .storage import ImageSaver, next_filename, ensure_space, archive_image
from .vision_jobs import VisionQueue, vision_defaults
from .triggers import PhotoelectricTrigger
from .processing import Processing
from .cl_config import Control, com_number
from .cl_preview import Preview

class Trigger:
    def __init__(self):
        self.reset()

    def reset(self):
        self.seen_low = False
        self.last_active = False
        self.deadline = None

    def update(self, status, now, capturing, post_ms):
        if not status["emergency_ok"]:
            self.reset()
            return "emergency"
        active = status["capture_active"]
        if not active:
            self.seen_low = True
        action = None
        if capturing:
            if active:
                self.deadline = None
            elif self.deadline is None:
                self.deadline = now + post_ms / 1000
            if self.deadline is not None and now >= self.deadline:
                action = "finish"
                self.deadline = None
        elif self.seen_low and active and not self.last_active:
            action = "start"
        self.last_active = active
        return action

class Engine(threading.Thread):
    def __init__(self, store, emit, data_root=None):
        super().__init__(name="CaptureController", daemon=True)
        self.store, self.emit = store, emit
        self.data_root = Path(data_root or ROOT / "data")
        self.commands = queue.Queue()
        self.encoder = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ImageEncoder")
        self.archiver = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ImageArchive")
        self.plc = self.camera = self.capture = None
        self.auto = self.simulation = self.exiting = False
        self.sim_status = dict(emergency_ok=True, front_sensor=False, rear_sensor=False,
                               capture_active=False, rear_sensor_seen=False,conveyor_running=True,
                               conveyor_start_command=False,conveyor_stop_command=False)
        self.plc_pulse=None
        self.trigger = self._make_trigger()
        self.signal_history = deque(maxlen=200)
        self.signal_counts = {k:0 for k in MAPPING}
        self.previous_raw = None
        self.last_plc_success = None
        self.pending = []
        self.archives = []
        self.last_poll = 0
        self.last_notice = 0
        self.status = None
        self.last_state = None
        self.saver = ImageSaver(store)
        self.vision = VisionQueue(self.data_root, emit)
        self.processing = Processing(store, self.data_root, emit)
        self.cl_control = Control(emit, self.data_root)
        self.cl_preview = None

    def _make_trigger(self):
        cfg = self.store.snapshot().get('trigger', {'mode':'plc_m020','debounce_ms':20})
        return PhotoelectricTrigger(cfg['debounce_ms']) if cfg['mode']=='photoelectric' else Trigger()

    def _record_signals(self, status):
        raw = status.copy() if self.simulation else getattr(self.plc, 'last_raw_status', status).copy()
        self.last_plc_success = datetime.now().astimezone().isoformat(timespec='milliseconds')
        if raw != self.previous_raw:
            if self.previous_raw is not None:
                for k in self.signal_counts:
                    self.signal_counts[k] += int(raw.get(k) != self.previous_raw.get(k))
            entry = {'time':self.last_plc_success,'raw':raw,'effective':status.copy()}
            self.signal_history.append(entry)
            symbols = self.store.snapshot()['devices']
            detail = '  '.join(f"{symbols[k]['symbol']}={int(raw[k]) if k in raw else '?'}" for k in self.signal_counts)
            self.log('PLC输入变化（原始值）：' + detail)
            self.emit('signals', dict(entry, changes=self.signal_counts.copy()))
            self.previous_raw = raw.copy()

    def _trigger_hint(self):
        if not self.plc: return '等待连接PLC并读取输入'
        if self.status and not self.status['emergency_ok']: return 'X000开关许可为0：梯形图将复位M10/M20/M21，请使开关许可有效'
        if self.plc_pulse:
            return ('正在发送M300运行确认，等待M010=1' if self.plc_pulse.key==START else '正在发送M400停止确认，等待M010=0')+'；阶段：'+self.plc_pulse.phase
        if not isinstance(self.trigger,PhotoelectricTrigger) and self.status and not self.status.get('conveyor_running',True):
            if not self.store.snapshot()['control']['confirm_run_on_arm']:
                return 'M010运行确认为0，M300自动确认选项已关闭；请核对现场运行确认方式'
            return 'M010运行确认未启用，PLC不会置位M020；移开板材后点击“开启自动采集”发送M300确认'
        if not self.auto: return '自动采集关闭；可先观察X1/X2及M10/M20/M21状态'
        if isinstance(self.trigger, PhotoelectricTrigger): return self.trigger.hint(bool(self.capture))
        if not self.trigger.seen_low: return 'M020模式：等待M020=0，避免从半块板开始'
        if not self.capture:
            if self.status and self.status['front_sensor'] and not self.status['capture_active']:
                return 'X1已检测到板，软件读取的M020仍为0；请导出现场状态核对M020/M021读取值'
            return 'M020模式：等待PLC置位M020（0→1）'
        return 'M020模式：正在采集，等待M020复位后结束'

    def export_diagnostics(self):
        cfg = self.store.snapshot()
        info = None
        if self.camera:
            i = self.camera.info
            info = {k:getattr(i,k) for k in ('width','height','trigger','multiply','divide','capture_signal')}
            info['description'] = self.camera.description
            info['working'] = bool(self.camera.working())
            info['stats'] = self.camera.stats()
        path = self.data_root/'diagnostics'/('现场状态_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
        atomic_json(path, {'version':'2026.09.19 XCIS658 / R6 recognition','config':cfg,'camera':info,'simulation':self.simulation,
            'plc_command':None if not self.plc_pulse else {'key':self.plc_pulse.key,'phase':self.plc_pulse.phase},
            'auto':self.auto,'capturing':bool(self.capture),'waiting_reason':self._trigger_hint(),
            'last_plc_success':self.last_plc_success,'effective_signals':self.status,
            'raw_signals':self.previous_raw,'changes':self.signal_counts,'signal_history':list(self.signal_history)})
        self.emit('diagnostics_exported', str(path))
        self.log('现场状态已导出：'+str(path))

    def enqueue_recognition(self, target):
        try:
            self.processing.enqueue(target, self.store.snapshot(), self.simulation)
        except Exception as e:
            self.emit('processing_error', '原图已保存，加入在线处理失败：' + str(e))

    def submit(self, action, **kwargs):
        self.commands.put((action, kwargs))

    def log(self, message, level="info"):
        self.emit("log", {"message": message, "level": level})

    def idle_required(self, allow_calibration=False):
        if self.auto or self.capture or self.pending or self.plc_pulse:
            raise RuntimeError("请先停止自动采集并等待图片保存完成")
        if self.cl_preview and not allow_calibration:
            raise RuntimeError('请先在“相机灯光与校正”页面停止校正预览')

    def _stop_cl_preview(self):
        if self.cl_preview:
            preview = self.cl_preview
            preview.stop()
            self.cl_preview = None
            self.log('校正预览已停止；生产图片编号未改变')

    def _cl_command(self, action, args):
        """Reject tuning while producing boards, without aborting production."""
        try:
            self.idle_required(allow_calibration=True)
            if self.simulation:
                raise RuntimeError('离线模拟模式不连接真实相机校正SDK，请断开设备后切换真实模式')
            if action == 'ports':
                ports = self.cl_control.enumerate()
                message = f'找到{len(ports)}个控制端口；请选择相机对应端口'
            elif action == 'connect':
                if self.cl_preview:
                    raise RuntimeError('更换控制端口前请先停止校正预览')
                self.cl_control.connect(args['port'], self.store.snapshot()['serial']['port'])
                message = '相机控制端口已连接并回读参数；未修改灯光或校正设置'
            elif action == 'disconnect':
                self._stop_cl_preview()
                self.cl_control.close()
                message = '相机控制端口已断开'
            elif action == 'read':
                self.cl_control.read()
                message = '已重新读取相机当前参数'
            elif action == 'preview_start':
                if not self.camera or not self.cl_control.connected:
                    raise RuntimeError('请先连接采集相机和对应的相机控制端口')
                if self.cl_preview:
                    raise RuntimeError('校正预览已启动')
                preview = Preview(self.camera, self.emit, self.store.snapshot()['camera']['no_frame_timeout_s'])
                preview.start()
                self.cl_preview = preview
                message = '校正预览已启动，等待图像；采集卡与控制端口须连接同一相机'
            elif action == 'preview_stop':
                self._stop_cl_preview()
                message = '校正预览已停止'
            elif action == 'save_preview':
                if not self.cl_preview or not self.cl_preview.ready():
                    raise RuntimeError('请先启动校正预览并等待新图像')
                folder = self.data_root / 'camera_calibration'
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / ('预览原始像素_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.png')
                size = self.cl_preview.save(path)
                message = f'已保存最近的预览条带 {size[0]}×{size[1]}px（不是整板图）：{path}'
                self.emit('cl_audit', str(path))
            elif action == 'export':
                path = self.cl_control.export()
                self.emit('cl_audit', str(path))
                message = '已导出相机参数：' + str(path)
            else:
                if not self.cl_control.connected:
                    raise RuntimeError('请先连接相机控制端口')
                if action == 'generate' and (not self.cl_preview or not self.cl_preview.ready() or not self.camera.working()):
                    raise RuntimeError('校正要求采集卡持续收到图像；请先启动校正预览并检查行触发')
                message = self.cl_control.perform(action, args)
            self.log(message)
            self.emit('cl_done', {'ok': True, 'message': message})
        except Exception as e:
            self.log('相机灯光/校正操作未完成：' + str(e), 'error')
            self.emit('cl_done', {'ok': False, 'message': str(e)})
        finally:
            self._state()

    def _read_current_status(self):
        self.status=self.sim_status.copy() if self.simulation else self.plc.read_status()
        self._record_signals(self.status)
        self.emit('plc',self.status)
        return self.status

    def _write_plc_command(self,key,on):
        symbol=self.store.snapshot()['devices'][key]['symbol']
        if self.simulation:
            self.sim_status[key]=on
            if on and key==START and self.sim_status['emergency_ok'] and not self.sim_status[STOP]:
                self.sim_status['conveyor_running']=True
            if on and key==STOP:
                self.sim_status.update(conveyor_running=False,capture_active=False,rear_sensor_seen=False)
        else:self.plc.write_command(key,on)
        self.log(f'PLC运行确认命令：{symbol}={int(on)}'+('（模拟）' if self.simulation else '（PLC已应答）'))

    def _cancel_plc_command(self):
        pulse=self.plc_pulse
        self.plc_pulse=None
        if pulse:
            try:pulse.cancel()
            except Exception as e:
                self.log('命令位清零未确认，请检查M300/M400状态：'+str(e),'error')
                raise

    def _request_plc_command(self,key):
        self._cancel_plc_command()
        self.plc_pulse=CommandPulse(self._write_plc_command,key,self.store.snapshot()['control'],time.monotonic())

    def connect_plc(self):
        if self.plc:
            return
        if self.cl_control.connected:
            port = com_number(self.cl_control.port['identifier'])
            if port is not None and port == com_number(self.store.snapshot()['serial']['port']):
                raise RuntimeError('PLC端口正在用于相机控制，请断开相机控制端口并核对接线')
        if self.simulation:
            self.plc = "simulation"
            self.status = self.sim_status.copy()
        else:
            plc = PLC(self.store.snapshot())
            try:
                self.status = plc.read_status()  # a port open alone is never 'connected'
            except Exception:
                plc.close()
                raise
            self.plc = plc
        self.trigger.reset()
        self.log("PLC连接成功：已读到X000/X001/X002、M010/M020/M021及M300/M400状态；连接操作未写入PLC")
        self.previous_raw = None
        self._record_signals(self.status)

    def connect_camera(self):
        if self.camera:
            return
        camera = SimCamera() if self.simulation else NativeCamera()
        try:
            camera.connect(self.store.snapshot()["camera"])
        except Exception:
            camera.close()
            raise
        self.camera = camera
        i = camera.info
        self.log(f"相机/采集卡已连接：{camera.description}；宽度{i.width}px；{i.height}行/块；{i.channels}通道；行触发/编码器沿用厂家配置，采集起止由上位机控制")
        self.emit("camera", {"description": camera.description, "width": i.width, "trigger": i.trigger})

    def _start_capture(self):
        if self.cl_preview:
            raise RuntimeError('请先停止校正预览，再开始木板采图')
        if not self.camera:
            raise RuntimeError("相机未连接")
        if self.capture:
            return
        if len(self.pending) >= 3:
            raise RuntimeError("图片编码积压达到3张，已停止自动采集；请降低进板速度或改用BMP")
        cfg = self.store.snapshot()
        # During a pending save, the old number may already have been published
        # while its counter commit is still in progress. The serial encoder checks
        # each final name itself; don't falsely reject the next board in that gap.
        if not self.pending:
            next_filename(cfg["output"])
        ensure_space(self.data_root / "spool", cfg["output"]["minimum_free_gb"], cfg["camera"]["max_capture_bytes"])
        ensure_space(cfg["output"]["directory"], cfg["output"]["minimum_free_gb"])
        capture_config = cfg["camera"].copy()
        if self.simulation:
            capture_config["minimum_height"] = 1
        self.capture = RawCapture(self.camera, self.data_root / "spool", capture_config)
        self.log('准备调用相机StartCapture：'+('X1/X2直接光电触发' if isinstance(self.trigger,PhotoelectricTrigger) and self.auto else 'M020触发' if self.auto else '手动采集'))
        try:
            self.capture.start()
        except Exception:
            self.capture = None
            raise
        self.log("开始采集，编号将在完整图片保存成功后推进")

    def _finish_capture(self, reason=""):
        if not self.capture:
            return
        capture = self.capture
        meta_path, meta = capture.finish(reason)
        self.capture = None
        if meta["status"] != "ready":
            self.auto = False
            message="本次采集未保存："+"；".join(meta["reasons"])+f"。已接收{meta['height']}行；原始暂存及原因：{meta_path}"
            self.log(message, "error")
            if meta.get('camera_diagnostics'):self.log('IKap停止诊断：'+meta['camera_diagnostics'],'error')
            self.emit('error',message)
            return
        self.pending.append(self.encoder.submit(self.saver.save, meta_path, meta))
        self.log(f"已收到{meta['height']}行，丢行0，正在保存图片")

    def _fault(self, message):
        self.auto = False
        try:self._cancel_plc_command()
        except Exception:pass
        self.trigger.reset()
        if self.capture:
            try:
                self._finish_capture(message)
            except Exception as e:
                self.log(str(e), "error")
        self.log(message, "error")
        self.emit("error", message)

    def _command(self, action, args):
        if action.startswith('cl_'):
            self._cl_command(action[3:], args)
            return
        if action in ("plc_connect", "camera_connect") and "config" in args:
            self.idle_required()
            if not self.plc and not self.camera:
                self.store.save(args["config"])
        if action == "plc_connect":
            self.connect_plc()
        elif action == "camera_connect":
            self.connect_camera()
        elif action == "disconnect":
            self.auto = False
            self._stop_cl_preview()
            self.cl_control.close()
            self._finish_capture("用户断开设备")
            if self.plc and self.plc != "simulation":
                self.plc.close()
            self.plc = None
            if self.camera:
                self.camera.close()
            self.camera = None
            self.status = None
            self.trigger.reset()
        elif action == "simulation":
            self.idle_required()
            if self.plc or self.camera or self.cl_control.connected:
                raise RuntimeError("切换模拟模式前请断开设备")
            self.simulation = args["enabled"]
            self.log("已切换为" + ("离线模拟模式" if self.simulation else "真实设备模式"))
        elif action == "save_config":
            self.idle_required()
            if self.plc or self.camera:
                raise RuntimeError("修改设备/采集参数前请先断开设备")
            self.store.save(args["config"])
            self.trigger = self._make_trigger()
            self.emit("config", self.store.snapshot())
            self.log("设置已保存")
        elif action == "arm":
            if self.cl_preview:
                raise RuntimeError('请先停止校正预览，再开启自动采图')
            if not self.plc or not self.camera:
                raise RuntimeError("请先连接PLC和相机")
            if self.capture:
                raise RuntimeError("请先结束手动采集")
            if self.pending:
                raise RuntimeError("请等待上一张图片保存完成")
            if self.plc_pulse:raise RuntimeError('PLC确认命令尚未完成，请稍候')
            current=self._read_current_status()
            if not current['emergency_ok']:raise RuntimeError('X000开关许可为0，请先使PLC许可有效')
            next_filename(self.store.snapshot()["output"])
            self.auto = True
            self.trigger = self._make_trigger()
            self.last_poll = 0
            if isinstance(self.trigger, PhotoelectricTrigger):
                self.log('自动采集已开启：X1/X2直接光电模式；先等待两光电无遮挡，X1有板开始，X2见板后板尾离开结束；M020不参与触发')
            else:
                self.log("自动采集已开启：M020模式，先等待M020=0，再接受下一次0→1")
                if self.store.snapshot()['control']['confirm_run_on_arm']:
                    if current[STOP]:raise RuntimeError('M400停止确认保持为1，请先点击停止自动采集清零后重试')
                    if not current['conveyor_running']:
                        if any(current[k] for k in ('front_sensor','rear_sensor','capture_active')):
                            raise RuntimeError('请先移开板材，等待X1/X2及M20均为0后，再开启自动采集')
                        self._request_plc_command(START)
                    else:self.log('M010运行确认已有效，直接等待M020触发，无需再次发送M300')
        elif action == "disarm":
            self.auto = False
            self._cancel_plc_command()
            if self.plc and not isinstance(self.trigger,PhotoelectricTrigger) and self.store.snapshot()['control']['confirm_run_on_arm']:
                self._request_plc_command(STOP)
            self._finish_capture("用户停止自动采集，当前板材可能不完整")
            self.trigger.reset()
        elif action == "manual_start":
            if self.auto:
                raise RuntimeError("自动采集已开启")
            if self.plc and self.status and not self.status["emergency_ok"]:
                raise RuntimeError("急停输入未释放")
            self._start_capture()
        elif action == "manual_stop":
            if not self.auto:
                self._finish_capture()
        elif action == "sim_signal":
            if not self.simulation:
                raise RuntimeError("真实模式不允许模拟输入")
            self.sim_status.update(args)
        elif action == 'export_diagnostics':
            try: self.export_diagnostics()
            except Exception as e: self.log('导出现场状态失败：'+str(e),'error')
        elif action == "shutdown":
            self.exiting = True
            self.auto = False
        else:
            raise ValueError(f"未知命令：{action}")

    def _tick(self):
        now = time.monotonic()
        cfg = self.store.snapshot()
        if self.cl_preview:
            stats = self.camera.stats()
            problem = self.cl_preview.error
            if any(stats.get(k, 0) for k in ('broken', 'failed', 'dropped', 'lost', 'flagged')):
                problem = problem or '校正预览采流异常，请停止并核对采集卡/行触发'
            if problem:
                self._stop_cl_preview()
                self.emit('cl_done', {'ok': False, 'message': problem})
                self.log(problem, 'error')
        if self.plc and now-self.last_poll >= cfg["serial"]["poll_interval_ms"]/1000:
            self.last_poll = now
            try:
                status = self.sim_status.copy() if self.simulation else self.plc.read_status()
            except Exception as e:
                self._fault("PLC通讯中断：" + str(e))
                if self.plc != "simulation":
                    self.plc.close()
                self.plc = None
                self.status = None
                return
            self.status = status
            self._record_signals(status)
            self.emit("plc", status)
            if self.plc_pulse:
                if self.plc_pulse.key==START and not status['emergency_ok']:
                    raise RuntimeError('X000许可关闭，已取消M300运行确认')
                if self.plc_pulse.tick(status,time.monotonic()):
                    self.log('PLC运行确认完成：M010='+str(int(status['conveyor_running']))+'，命令位已清零')
                    self.plc_pulse=None
            if (self.auto and not isinstance(self.trigger,PhotoelectricTrigger)
                    and cfg['control']['confirm_run_on_arm'] and not status['conveyor_running']
                    and not (self.plc_pulse and self.plc_pulse.key==START)):
                self._fault('M010运行确认已撤销，自动采集停止；请检查X000/M400并重新开启')
                return
            if self.capture and not status["emergency_ok"]:
                self._fault("急停输入断开，结束当前采集")
                return
            if self.auto:
                action = self.trigger.update(status, time.monotonic(), bool(self.capture), cfg["camera"]["post_trigger_ms"])
                if action == "emergency":
                    self._fault("急停输入未释放，自动采集已停止")
                elif action == "start":
                    self._start_capture()
                elif action == "finish":
                    self._finish_capture()
        if self.camera and not self.simulation:
            if self.camera.stats()["broken"]:
                self._fault("相机网络连接断开，请检查网线/供电后重新连接")
                self.camera.close()
                self.camera = None
                return
        if self.capture:
            if self.capture.error:
                self._fault("写盘失败：" + self.capture.error)
            elif now-self.capture.started > cfg["camera"]["max_capture_seconds"]:
                self._fault("单板采集超时，请检查后光电及M020")
            elif now-self.capture.last_frame > cfg["camera"]["no_frame_timeout_s"]:
                self._fault("长时间没有收到相机图像，请检查触发信号、编码器、供电及网络")
            elif self.camera.stats()["failed"] or not self.camera.working():
                self._fault("相机采流意外停止")
        for future in self.pending[:]:
            if future.done():
                self.pending.remove(future)
                try:
                    target, archive = future.result()
                    self.emit("saved", {"path": str(target), "next_number": self.store.snapshot()["output"]["next_number"]})
                    self.log("图片保存成功：" + str(target))
                    self.enqueue_recognition(target)
                    if archive:
                        if len(self.archives) >= 20:
                            self.log("归档积压达到20张，本张仍保留在本机，请稍后手动归档", "error")
                        else:
                            self.archives.append(self.archiver.submit(archive_image, target, archive))
                except Exception as e:
                    self._fault("图片保存失败，原始暂存和编号保留：" + str(e))
        for future in self.archives[:]:
            if future.done():
                self.archives.remove(future)
                try:
                    self.log("归档校验完成：" + str(future.result()))
                except Exception as e:
                    self.log("归档失败，本机原图保留：" + str(e), "error")
        self._state()

    def _state(self):
        state = dict(plc=bool(self.plc), camera=bool(self.camera), auto=self.auto,
                     capture=bool(self.capture), saving=len(self.pending), archives=len(self.archives),
                     simulation=self.simulation, lines=self.capture.written//self.capture.info["stride"] if self.capture else 0)
        state['trigger_hint'] = self._trigger_hint()
        state['trigger_mode'] = self.store.snapshot().get('trigger',{}).get('mode','plc_m020')
        state['plc_command_busy']=bool(self.plc_pulse)
        state['cl_connected'] = self.cl_control.connected
        state['cl_preview'] = self.cl_preview is not None
        if state != self.last_state:
            self.emit("state", state)
            self.last_state = state

    def run(self):
        self.vision.start()
        self.processing.start()
        try:
            while not self.exiting:
                try:
                    action, args = self.commands.get(timeout=0.01)
                    try:
                        self._command(action, args)
                    except Exception as e:
                        self._fault(str(e))
                except queue.Empty:
                    pass
                try:
                    self._tick()
                except Exception as e:
                    self._fault(str(e))
                    self._state()
        finally:
            try:self._cancel_plc_command()
            except Exception:pass
            try:
                self._stop_cl_preview()
                self.cl_control.close()
            except Exception as e:
                self.log('关闭相机校正接口失败：' + str(e), 'error')
            try:
                self._finish_capture("程序关闭，当前采集未完成")
                if self.camera:
                    self.camera.close()
                if self.plc and self.plc != "simulation":
                    self.plc.close()
            except Exception as e:
                self.log("关闭设备失败：" + str(e), "error")
            # Normal encodes finish before exit; external disk work must not block the GUI thread.
            self.encoder.shutdown(wait=True)
            self.archiver.shutdown(wait=True)
            for future in self.pending:
                try:
                    path, archive = future.result()
                    self.enqueue_recognition(path)
                    if archive:
                        self.log("关闭期间保存完成，尚未归档：" + str(path), "warning")
                except Exception as e:
                    self.log("关闭期间保存失败，暂存保留：" + str(e), "error")
            self.processing.close()
            self.vision.close()
            self.emit("closed", None)
