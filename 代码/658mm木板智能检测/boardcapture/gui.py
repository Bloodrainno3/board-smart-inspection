from __future__ import annotations
import copy
from datetime import datetime
import logging
from pathlib import Path
import os
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap, QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QLineEdit, QComboBox,
    QSpinBox, QCheckBox, QFormLayout, QHBoxLayout, QVBoxLayout, QGridLayout, QGroupBox,
    QFileDialog, QPlainTextEdit, QScrollArea, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
)
from serial.tools import list_ports
from .config import MAPPING, ROOT, validate
from .engine import Engine
from .theme import apply_light_theme

class Events(QObject):
    event = Signal(str, object)

class Window(QMainWindow):
    def __init__(self, store, start_engine=True):
        super().__init__()
        apply_light_theme(QApplication.instance())
        self.store = store
        self.state = {}
        self.closed_ok = False
        self.closing = False
        self.setWindowTitle("658mm木板智能检测 · 2026.09.23 相机校正版")
        self.setMinimumSize(980, 600)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1380, screen.width()-30), min(920, screen.height()-40))
        self.events = Events()
        self.events.event.connect(self.on_event)
        self.engine = Engine(store, self.events.event.emit)
        self.fields = {}
        self.build()
        self.load_fields(store.snapshot())
        for message in store.notices:
            self.log(message)
        self.log("配置已加载。连接PLC仅读取状态；开启/停止自动采集发送M300/M400运行确认。现场传送带由变频器手动控制。")
        self.log("串口继承原版：19200 / 8O1；历史成功记录为8E1。如无响应，请核对现场校验位。")
        self.log('PLC链路：M300确认→M010有效→X1置位M020→相机采图；M021配合X2结束。X000是梯形图许可条件，须为ON。')
        self.log('R6识别：按横向覆盖保留倾斜缝；宽缝由上下模型边界与原图色带配对补全，须复核。原始模型掩膜保留。')
        self.log('658相机版：IKapLibrary 1.7.3；连接前选择厂家 .vlcf 配置。换相机后请重新进行现场距离标定。')
        self.refresh_ports()
        self.update_state(dict(plc=False, camera=False, auto=False, capture=False, saving=0, archives=0, simulation=False, lines=0))
        if start_engine:
            self.engine.start()

    def combo(self, options):
        box = QComboBox()
        for label, value in options:
            box.addItem(label, value)
        return box

    def spin(self, low, high):
        field = QSpinBox()
        field.setRange(low, high)
        return field

    def button(self, text, callback):
        btn = QPushButton(text)
        btn.clicked.connect(callback)
        return btn

    def path_row(self, field, directory=True):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        def choose():
            value = QFileDialog.getExistingDirectory(self, "选择目录", field.text()) if directory else QFileDialog.getOpenFileName(self, "选择IKap采集配置", field.text(), "IKap采集配置 (*.vlcf);;所有文件 (*)")[0]
            if value:
                field.setText(value)
        layout.addWidget(self.button("浏览…", choose))
        return row

    def build(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        title = QLabel("658mm木板智能检测")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch()
        self.mode = QCheckBox("离线模拟（不连接设备）")
        self.mode.clicked.connect(lambda checked: self.engine.submit("simulation", enabled=checked))
        header.addWidget(self.mode)
        layout.addLayout(header)
        self.banner = QLabel("准备就绪：设置保存目录和编号，然后连接PLC与相机")
        self.banner.setWordWrap(True)
        self.banner.setObjectName("banner")
        layout.addWidget(self.banner)
        from .processing_gui import ProcessingPanel
        self.processing_panel = ProcessingPanel(self.store, self.engine.processing, self.log)
        layout.addWidget(self.processing_panel.quick_controls)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.main_tabs = QTabWidget()
        layout.addWidget(self.main_tabs, 1)
        self.main_tabs.addTab(split, '设备与自动采图')
        self.settings_tabs = QTabWidget()
        main_settings = QWidget()
        form_stack = QVBoxLayout(main_settings)
        connection = QGroupBox("1  PLC通讯 · USB → RS485")
        form = QFormLayout(connection)
        self.port = QComboBox()
        self.port.setEditable(True)
        port_row = QWidget()
        ph = QHBoxLayout(port_row)
        ph.setContentsMargins(0,0,0,0)
        ph.addWidget(self.port, 1)
        ph.addWidget(self.button("检测串口", self.refresh_ports))
        form.addRow("COM端口", port_row)
        self.baud = self.combo([(str(n), n) for n in (9600,19200,38400,57600,115200)])
        self.parity = self.combo([("奇校验 O（原版当前配置）", "O"), ("偶校验 E（历史成功记录）", "E"), ("无校验 N", "N")])
        self.stopbits = self.combo([("1", 1), ("2", 2)])
        self.slave = self.spin(1,247)
        form.addRow("波特率", self.baud)
        form.addRow("校验位", self.parity)
        form.addRow("停止位 / 数据位8", self.stopbits)
        form.addRow("PLC站号", self.slave)
        form_stack.addWidget(connection)
        camera = QGroupBox("2  XCIS658 · IKap Camera Link采集卡")
        cf = QFormLayout(camera)
        self.serial = QLineEdit()
        self.serial.setPlaceholderText("单个设备可留空；多个时填索引0、1…")
        self.grabber_config_file = QLineEdit()
        self.grabber_config_file.setPlaceholderText("厂家调通相机后导出的 .vlcf 文件")
        cf.addRow("采集卡索引", self.serial)
        trigger_box=QGroupBox('自动拍照触发')
        tf=QFormLayout(trigger_box)
        self.trigger_mode = self.combo([('PLC M020采集状态（默认，原梯形图）','plc_m020'),
                                        ('X1/X2直接光电（可选）','photoelectric')])
        self.trigger_debounce = self.spin(0,500)
        tf.addRow('触发依据',self.trigger_mode)
        tf.addRow('直接光电模式消抖毫秒',self.trigger_debounce)
        self.confirm_run=QCheckBox('开启/停止自动采集时发送M300/M400运行确认（原梯形图）')
        tf.addRow(self.confirm_run)
        trigger_note=QLabel('先移开板材，X000许可须为ON。开启时发送M300脉冲，确认M010=1后等待M020采图。\nM021由PLC配合X2结束；停止时发送M400脉冲复位运行确认。\n现场变频器由人工启动，与PLC无连接。')
        trigger_note.setWordWrap(True);tf.addRow(trigger_note)
        form_stack.insertWidget(0,trigger_box)
        cf.addRow("采集配置（必选）", self.path_row(self.grabber_config_file, False))
        note = QLabel("先在IKapExpert中调通并导出配置，然后关闭厂家程序。\n自动读取配置宽度/颜色，保留行触发及编码器设置；PLC控制采集起止。\n658是毫米幅宽，不是像素数。更换相机后重新做距离标定。")
        note.setWordWrap(True)
        cf.addRow(note)
        form_stack.addWidget(camera)
        output = QGroupBox("图片保存与数字命名")
        of = QFormLayout(output)
        self.output_path = QLineEdit()
        self.format = self.combo([(v,v) for v in ("JPG","BMP","PNG","TIFF")])
        self.numbering = self.combo([("数字正序（每张 +1）", "ascending"), ("数字倒序（每张 −1）", "descending"), ("时间戳", "timestamp")])
        self.next_number = QLineEdit()
        self.end_number = QLineEdit()
        self.end_number.setPlaceholderText("可留空，不限制终止编号")
        self.filename_example = QLabel()
        self.filename_example.setWordWrap(True)
        of.addRow("保存目录", self.path_row(self.output_path))
        of.addRow("图片格式", self.format)
        of.addRow("命名方式", self.numbering)
        of.addRow("起始 / 下一编号", self.next_number)
        of.addRow("终止编号（可选）", self.end_number)
        of.addRow(self.filename_example)
        for widget in (self.next_number, self.end_number):
            widget.textChanged.connect(self.example)
        self.numbering.currentIndexChanged.connect(self.example)
        self.format.currentIndexChanged.connect(self.example)
        save_page = QWidget()
        save_layout = QVBoxLayout(save_page)
        save_layout.addWidget(output)
        guide = QLabel("使用顺序\n\n① 设置图片目录、格式和下一编号。\n② 在“PLC与相机”中确认默认PLC M020触发并连接设备。\n③ 检查X0急停正常，以及M020/M021状态。\n④ 移开板材，点击“开启自动采集”。\n\n默认沿用原梯形图：M020的0→1开始采图，1→0后续采并保存；M021由PLC辅助处理后光电经过状态。\n\n正序：21、22、23……\n倒序：100、99、98……\n\n未采集、已连接和采集中都可切换页面；修改运行参数请先停止并断开设备。")
        guide.setWordWrap(True)
        guide.setStyleSheet("padding:18px 8px; color:#50657d; line-height:1.6;")
        save_layout.addWidget(guide)
        save_layout.addStretch()
        form_stack.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(main_settings)
        save_scroll = QScrollArea()
        save_scroll.setWidgetResizable(True)
        save_scroll.setWidget(save_page)
        self.settings_tabs.addTab(save_scroll, "保存与命名")
        self.settings_tabs.addTab(scroll, "PLC与相机")
        advanced = QWidget()
        adv = QFormLayout(advanced)
        self.expected_width = self.spin(0,65535)
        self.post_ms = self.spin(0,5000)
        self.min_height = self.spin(1,1000000)
        self.max_height = self.spin(1,1000000)
        self.queue_mb = self.spin(16,2048)
        self.max_seconds = self.spin(1,3600)
        self.no_frame = self.spin(1,300)
        self.quality = self.spin(1,100)
        self.reserve = self.spin(0,1000)
        self.pixel_order = self.combo([("自动读取IKap格式，统一RGB", "RGB")])
        self.archive_path = QLineEdit()
        self.archive_path.setPlaceholderText("留空关闭；归档后保留本机原图")
        for name, widget in (("预期宽度（0=自动）",self.expected_width), ("尾部续采毫秒",self.post_ms),
            ("最小有效行数",self.min_height), ("最大有效行数",self.max_height), ("接收缓存MB",self.queue_mb),
            ("单板最长秒数",self.max_seconds), ("无图像超时秒数",self.no_frame), ("JPG质量",self.quality),
            ("磁盘保留GB",self.reserve), ("像素顺序",self.pixel_order)):
            adv.addRow(name,widget)
        adv.addRow("异步归档目录",self.path_row(self.archive_path))
        self.mapping = QTableWidget(len(MAPPING),4)
        self.mapping.setHorizontalHeaderLabels(["信号","区域","0基地址","高电平有效"])
        self.mapping.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.mapping.setMinimumHeight(290)
        adv.addRow(self.mapping)
        adv_note = QLabel("读取：X用02，M用01；M300/M400确认脉冲用05，0→1→0。\n不直接写M010/M020/M021。修改设置前请断开设备。")
        adv_note.setWordWrap(True)
        adv.addRow(adv_note)
        ascroll = QScrollArea()
        ascroll.setWidgetResizable(True)
        ascroll.setWidget(advanced)
        self.settings_tabs.addTab(ascroll,"高级与映射")
        self.lockable_sections = [trigger_box,connection,camera,output,advanced]
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0,0,0,0)
        lv.addWidget(self.settings_tabs)
        self.lock_notice = QLabel('参数可编辑')
        self.lock_notice.setWordWrap(True)
        lv.addWidget(self.lock_notice)
        self.apply = self.button("保存设置", self.apply_settings)
        lv.addWidget(self.apply)
        split.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8,0,0,0)
        self.summary = QLabel()
        self.summary.setObjectName("summary")
        self.summary.setWordWrap(True)
        rv.addWidget(self.summary)
        buttons = QHBoxLayout()
        self.plc_btn = self.button("连接 / 只读检测PLC", lambda: self.connect_device("plc_connect"))
        self.camera_btn = self.button("连接相机", lambda: self.connect_device("camera_connect"))
        self.disconnect_btn = self.button("断开设备", lambda: self.engine.submit("disconnect"))
        for b in (self.plc_btn,self.camera_btn,self.disconnect_btn):
            buttons.addWidget(b)
        rv.addLayout(buttons)
        auto_row = QHBoxLayout()
        self.arm = self.button("开启自动采集", lambda: self.engine.submit("arm"))
        self.arm.setObjectName("primary")
        self.disarm = self.button("停止自动采集 / 复位运行确认", lambda: self.engine.submit("disarm"))
        auto_row.addWidget(self.arm)
        auto_row.addWidget(self.disarm)
        rv.addLayout(auto_row)
        self.trigger_status = QLabel('等待连接设备')
        self.trigger_status.setObjectName('triggerStatus')
        self.trigger_status.setWordWrap(True)
        rv.addWidget(self.trigger_status)
        lamps = QGridLayout()
        self.signal_lamps = {}
        self.signal_names = {'emergency_ok':'X0 开关许可','front_sensor':'X1 前光电',
                            'rear_sensor':'X2 后光电','conveyor_running':'M010 运行确认',
                            'capture_active':'M020 采集','rear_sensor_seen':'M021 经过',
                            'conveyor_start_command':'M300 启用命令','conveyor_stop_command':'M400 停止命令'}
        for index,(key,name) in enumerate(self.signal_names.items()):
            lamp=QLabel(name+'\n未连接')
            lamp.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lamp.setMinimumHeight(48)
            lamp.setStyleSheet('background:#e3e9f0;color:#3d5067;border:1px solid #acbbce;border-radius:5px;padding:4px;')
            self.signal_lamps[key]=lamp
            lamps.addWidget(lamp,index//4,index%4)
        rv.addLayout(lamps)
        self.sensor_text = QLabel("PLC输入尚未读取")
        self.sensor_text.setWordWrap(True)
        rv.addWidget(self.sensor_text)
        self.camera_text = QLabel("相机尚未连接")
        self.camera_text.setWordWrap(True)
        rv.addWidget(self.camera_text)
        rv.addWidget(self.button('导出现场状态（包含输入变化和触发原因）',lambda:self.engine.submit('export_diagnostics')))
        self.preview = QLabel("保存后的图片将在此预览")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(300,140)
        self.preview.setObjectName("preview")
        rv.addWidget(self.preview,1)
        self.saved_text = QLabel("尚未保存图片")
        self.saved_text.setWordWrap(True)
        self.saved_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rv.addWidget(self.saved_text)
        manual = QHBoxLayout()
        self.manual_start = self.button("手动开始", lambda: self.engine.submit("manual_start"))
        self.manual_stop = self.button("手动结束并保存", lambda: self.engine.submit("manual_stop"))
        manual.addWidget(self.manual_start)
        manual.addWidget(self.manual_stop)
        manual.addWidget(self.button("打开图片目录", self.open_output))
        rv.addLayout(manual)
        self.sim_controls = QWidget()
        sim = QHBoxLayout(self.sim_controls)
        sim.setContentsMargins(0,0,0,0)
        sim.addWidget(self.button("模拟板头进入",lambda: self.engine.submit("sim_signal",front_sensor=True,capture_active=True,rear_sensor=False,rear_sensor_seen=False)))
        sim.addWidget(self.button("模拟经过后光电并离开",self.simulate_tail))
        rv.addWidget(self.sim_controls)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setWidget(right)
        split.addWidget(right_scroll)
        split.setSizes([510,840])
        from .grading_gui import GradingWindow
        self.grading_window = GradingWindow(self.store, self.log, self)
        grade_page=QWidget();grade_layout=QVBoxLayout(grade_page)
        grade_note=QLabel('本窗口用于手动分级；主界面上方也可选择采图＋木皮分级，按每张新采板图自动执行。')
        grade_note.setWordWrap(True);grade_layout.addWidget(grade_note)
        grade_layout.addWidget(self.button('打开木皮颜色分级窗口',self.open_grading_window));grade_layout.addStretch()
        self.main_tabs.addTab(grade_page,'木皮颜色分级')
        header.insertWidget(2,self.button('木皮分级',self.open_grading_window))
        from .vision_gui import VisionPanel, VisionWindow
        self.vision_panel = VisionPanel(self.store, self.engine.vision, self.log)
        self.vision_window = VisionWindow(self.vision_panel, self)
        launch=QWidget();launch_layout=QVBoxLayout(launch)
        note=QLabel('采图和板缝识别分别操作。默认只采图并保存到指定目录。\n打开独立窗口后，选择图片或文件夹，再点击“开始识别所选图片”。\n距离标定与已有结果查看也在该窗口中，无需连接PLC或相机。')
        note.setWordWrap(True);launch_layout.addWidget(note)
        launch_layout.addWidget(self.button('打开板缝识别与距离标定窗口',self.open_vision_window))
        launch_layout.addStretch()
        self.main_tabs.addTab(launch, '板缝识别与距离标定')
        header.insertWidget(2,self.button('打开板缝识别窗口',self.open_vision_window))
        self.main_tabs.addTab(self.processing_panel, '采图联动与实时输出')
        from .cl_config_gui import CameraCalibrationPanel
        self.cl_panel = CameraCalibrationPanel(self.engine.submit)
        self.main_tabs.addTab(self.cl_panel, '相机灯光与校正')
        self.processing_panel.changed.connect(lambda: self.vision_panel.enabled.setChecked(self.store.snapshot()['vision']['enabled']))
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1200)
        self.logs.setMaximumHeight(130)
        layout.addWidget(self.logs)
        self.setStyleSheet("""
            QMainWindow { background:#f2f5f9; } QWidget { font-family:'Microsoft YaHei UI'; font-size:13px; color:#172b43; }
            QLabel#title { font-size:25px; font-weight:700; padding:8px 0; }
            QLabel#banner { background:#e5efff; border:1px solid #cadcf4; padding:12px; border-radius:6px; }
            QLabel#summary { font-size:16px; font-weight:600; padding:10px; }
            QLabel#triggerStatus { background:#e5efff; color:#12497c; border:1px solid #cadcf4; padding:8px; border-radius:5px; }
            QTabWidget::pane { background:#f2f5f9; border:1px solid #bfccda; }
            QTabBar::tab { background:#e2eaf4; color:#263f59; border:1px solid #a8b9cc; padding:9px 13px; }
            QTabBar::tab:selected { background:#ffffff; color:#124a86; border-bottom:3px solid #1764c0; }
            QTabBar::tab:hover { background:#d6e8fd; }
            QScrollArea { background:#f2f5f9; border:0; }
            QCheckBox { color:#172b43; } QCheckBox:disabled { color:#5d6e80; }
            QHeaderView::section { background:#e6edf5; color:#172b43; padding:5px; }
            QTableWidget { background:white; color:#172b43; alternate-background-color:#edf3f9; gridline-color:#c9d4e1; }
            QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled { color:#5d6e80; background:#edf1f6; }
            QGroupBox { font-weight:600; border:1px solid #d7e0eb; border-radius:6px; margin-top:13px; padding:15px 8px 8px; background:white; }
            QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 5px; }
            QLineEdit,QComboBox,QSpinBox { background:white; border:1px solid #c9d4e1; border-radius:4px; padding:5px; min-height:22px; }
            QPushButton { background:white; border:1px solid #bacadc; border-radius:5px; padding:8px 10px; }
            QPushButton:hover { background:#eaf2fc; } QPushButton:disabled { color:#8b97a4; background:#edf0f4; }
            QPushButton#primary { background:#1764c0; color:white; border-color:#1764c0; font-weight:600; }
            QPushButton#primary:disabled { background:#b8c9df; border-color:#b8c9df; }
            QLabel#preview { background:#e3e9f0; border:1px solid #ccd7e3; border-radius:6px; color:#687c92; }
            QPlainTextEdit { background:#162334; color:#d5e3f3; border-radius:6px; padding:6px; font-size:12px; }
        """)

    def select(self, combo, data):
        index = combo.findData(data)
        if index < 0:
            combo.addItem(str(data), data)
            index = combo.count()-1
        combo.setCurrentIndex(index)

    def load_fields(self, cfg):
        s,c,o = cfg["serial"], cfg["camera"], cfg["output"]
        self.select(self.trigger_mode,cfg['trigger']['mode'])
        self.trigger_debounce.setValue(cfg['trigger']['debounce_ms'])
        self.confirm_run.setChecked(cfg['control']['confirm_run_on_arm'])
        self.port.setCurrentText(s["port"])
        for field, value in ((self.baud,s["baudrate"]),(self.parity,s["parity"]),(self.stopbits,s["stopbits"]),
            (self.format,o["format"]),(self.numbering,o["numbering"]),(self.pixel_order,c["pixel_order"])):
            self.select(field,value)
        self.slave.setValue(s["slave_id"])
        self.serial.setText(c["serial_number"])
        self.grabber_config_file.setText(c.get("grabber_config_file", ""))
        self.output_path.setText(o["directory"])
        self.next_number.setText(str(o["next_number"]))
        self.end_number.setText("" if o["end_number"] is None else str(o["end_number"]))
        self.archive_path.setText(o["archive_directory"])
        for field, value in ((self.expected_width,c["expected_width"]),(self.post_ms,c["post_trigger_ms"]),
            (self.min_height,c["minimum_height"]),(self.max_height,c["maximum_height"]),(self.queue_mb,c["queue_mb"]),
            (self.max_seconds,c["max_capture_seconds"]),(self.no_frame,c["no_frame_timeout_s"]),
            (self.quality,o["jpeg_quality"]),(self.reserve,o["minimum_free_gb"])):
            field.setValue(int(value))
        for row,key in enumerate(MAPPING):
            d=cfg["devices"][key]
            item=QTableWidgetItem(d["symbol"])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.mapping.setItem(row,0,item)
            area=self.combo([("输入 FC02","discrete_input"),("线圈 FC01","coil")])
            self.select(area,d["modbus_area"])
            self.mapping.setCellWidget(row,1,area)
            self.mapping.setItem(row,2,QTableWidgetItem(str(d["address"])))
            check=QCheckBox()
            check.setChecked(d["active_high"])
            self.mapping.setCellWidget(row,3,check)
        self.example()

    def config_from_fields(self):
        cfg=self.store.snapshot()
        s,c,o=cfg["serial"],cfg["camera"],cfg["output"]
        cfg['trigger'].update(mode=self.trigger_mode.currentData(),debounce_ms=self.trigger_debounce.value())
        cfg['control']['confirm_run_on_arm']=self.confirm_run.isChecked()
        s.update(port=self.port.currentText().split(" — ")[0].strip(), baudrate=self.baud.currentData(),
                 parity=self.parity.currentData(), stopbits=self.stopbits.currentData(), slave_id=self.slave.value())
        c.update(serial_number=self.serial.text().strip(), grabber_config_file=self.grabber_config_file.text().strip(),
                 expected_width=self.expected_width.value(),
                 post_trigger_ms=self.post_ms.value(), minimum_height=self.min_height.value(), maximum_height=self.max_height.value(),
                 queue_mb=self.queue_mb.value(), max_capture_seconds=self.max_seconds.value(), no_frame_timeout_s=self.no_frame.value(),
                 pixel_order=self.pixel_order.currentData())
        o.update(directory=self.output_path.text().strip(), format=self.format.currentData(), numbering=self.numbering.currentData(),
                 next_number=int(self.next_number.text().strip()), end_number=int(self.end_number.text()) if self.end_number.text().strip() else None,
                 jpeg_quality=self.quality.value(), minimum_free_gb=self.reserve.value(), archive_directory=self.archive_path.text().strip())
        for row,key in enumerate(MAPPING):
            cfg["devices"][key].update(address=int(self.mapping.item(row,2).text(),0) if self.mapping.item(row,2).text().startswith("0x") else int(self.mapping.item(row,2).text()),
                modbus_area=self.mapping.cellWidget(row,1).currentData(), active_high=self.mapping.cellWidget(row,3).isChecked())
        validate(cfg)
        return cfg

    def apply_settings(self):
        try:
            self.engine.submit("save_config",config=self.config_from_fields())
        except Exception as e:
            self.on_event("error",str(e))

    def connect_device(self,action):
        try:
            args={} if self.state.get("plc") or self.state.get("camera") else {"config":self.config_from_fields()}
            self.engine.submit(action,**args)
        except Exception as e:
            self.on_event("error",str(e))

    def refresh_ports(self):
        current=self.port.currentText().split(" — ")[0]
        self.port.clear()
        ports=list(list_ports.comports())
        for port in ports:
            self.port.addItem(f"{port.device} — {port.description}")
        match=next((i for i in range(self.port.count()) if self.port.itemText(i).startswith(current+" — ")),None)
        if match is not None:
            self.port.setCurrentIndex(match)
        else:
            self.port.setCurrentText(current)
        self.log("检测到串口："+("；".join(f"{p.device} {p.description}" for p in ports) or "无；请插入USB转RS485"))

    def example(self):
        try:
            n=int(self.next_number.text())
            direction=self.numbering.currentData()
            ext=(self.format.currentData() or "JPG").lower()
            step=1 if direction=="ascending" else -1
            values="、".join(f"{n+step*i}.{ext}" for i in range(3)) if direction != "timestamp" else "按拍摄时间命名"
            self.filename_example.setText("预览："+values+"\n仅完整图片保存成功才推进编号；图片目录不生成说明文件。")
        except ValueError:
            self.filename_example.setText("请输入整数起始编号，可使用负数；正序 +1，倒序 −1。")

    def open_output(self):
        path=Path(self.output_path.text())
        path.mkdir(parents=True,exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def log(self,message,level="info"):
        self.logs.appendPlainText(datetime.now().strftime("%H:%M:%S")+"  "+message)
        getattr(logging,level if level in ("info","warning","error") else "info")(message)

    def update_state(self,s):
        self.state=s
        connected=s["plc"] or s["camera"]
        busy=s["capture"] or s["auto"] or s["saving"] or s.get('plc_command_busy',False) or s.get('cl_preview',False)
        self.cl_panel.update_state(s.copy())
        self.settings_tabs.setEnabled(True)
        for section in self.lockable_sections: section.setEnabled(not connected and not busy)
        self.lock_notice.setText('运行参数只读；页面可以切换。修改前请停止采集并断开设备。' if connected or busy else '参数可编辑；保存后再连接设备。')
        self.apply.setEnabled(not connected and not busy)
        self.mode.setEnabled(not connected and not busy and not s.get('cl_connected'))
        self.mode.setChecked(s["simulation"])
        self.sim_controls.setVisible(s["simulation"])
        self.plc_btn.setEnabled(not s["plc"] and not s["capture"])
        self.camera_btn.setEnabled(not s["camera"] and not s["capture"])
        self.disconnect_btn.setEnabled(bool(connected or s.get('cl_connected')))
        self.arm.setEnabled(s["plc"] and s["camera"] and not busy)
        self.disarm.setEnabled(s["auto"] or bool(s['plc']))
        self.manual_start.setEnabled(s["camera"] and not busy)
        self.manual_stop.setEnabled(s["capture"] and not s["auto"])
        mode="模拟模式" if s["simulation"] else "真实设备"
        self.summary.setText(f"{mode}  |  PLC {'已连接' if s['plc'] else '未连接'}  |  相机 {'已连接' if s['camera'] else '未连接'}\n"
            f"{'自动采集中' if s['auto'] else '自动采集关闭'}  ·  当前 {s['lines']} 行  ·  待保存 {s['saving']} 张  ·  待归档 {s['archives']} 张")
        self.trigger_status.setText(s.get('trigger_hint','连接后请核对X1/X2光电状态'))
        if not s["plc"]:
            self.sensor_text.setText("PLC状态不可用")
            for key,lamp in self.signal_lamps.items():
                lamp.setText(self.signal_names[key]+'\n未连接')
                lamp.setStyleSheet('background:#e3e9f0;color:#3d5067;border:1px solid #acbbce;border-radius:5px;padding:4px;')
        if not s["camera"]:
            self.camera_text.setText("相机尚未连接")

    def on_event(self,kind,data):
        if kind.startswith('cl_'):
            self.cl_panel.handle_event(kind,data)
            return
        if kind.startswith('processing_'):
            self.processing_panel.handle_event(kind,data)
            return
        if kind.startswith('vision_') or kind == 'calibration_result':
            self.vision_panel.handle_event(kind,data)
            return
        if kind=="log":
            self.log(data["message"],data["level"])
            if data["level"]=="info" and data["message"].startswith(("PLC连接成功", "相机已连接", "自动采集已开启", "开始采集")):
                self.banner.setStyleSheet("")
                self.banner.setText(data["message"])
        elif kind=="error":
            self.banner.setText("需要处理："+str(data))
            self.banner.setStyleSheet("background:#fde9e7;color:#a02821;padding:12px;border-radius:6px;")
        elif kind=="state":
            self.update_state(data)
        elif kind=="config":
            self.load_fields(data)
            self.banner.setStyleSheet("")
            self.banner.setText("设置已保存，可以连接设备")
        elif kind=="plc":
            for key,lamp in self.signal_lamps.items():
                if key not in data:
                    lamp.setText(self.signal_names[key]+'\n未知');continue
                on=bool(data[key]);lamp.setText(self.signal_names[key]+('\n● ON 有效' if on else '\n○ OFF 无效'))
                bg,fg=('#dcf5e7','#135f39') if on else ('#e7edf5','#41546c')
                if key=='emergency_ok' and not on:bg,fg='#fde9e7','#a02821'
                lamp.setStyleSheet(f'background:{bg};color:{fg};border:1px solid {fg};border-radius:5px;padding:4px;font-weight:600;')
        elif kind=='signals':
            raw=data['raw'];changes=data['changes']
            self.sensor_text.setText('原始位：'+'  '.join(f"{self.signal_names[k].split()[0]}={int(raw[k]) if k in raw else '?'}" for k in self.signal_names)
                +f"\n输入变化次数：X1 {changes['front_sensor']} / X2 {changes['rear_sensor']} / M020 {changes['capture_active']}")
        elif kind=='diagnostics_exported':
            self.banner.setText('现场状态已保存：'+data)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(data).parent)))
        elif kind=="camera":
            self.camera_text.setText(data["description"]+f" · {data['width']} px · 触发模式{data['trigger']}")
        elif kind=="saved":
            self.saved_text.setText("已保存："+data["path"])
            self.next_number.setText(str(data["next_number"]))
            # QImageReader scaled decoding avoids retaining a full-size long image in the UI.
            from PySide6.QtGui import QImageReader
            from PySide6.QtCore import QSize
            reader=QImageReader(data["path"])
            size=reader.size()
            size.scale(self.preview.size(),Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)
            image=reader.read()
            if not image.isNull():
                self.preview.setPixmap(QPixmap.fromImage(image))
        elif kind=="closed":
            self.closed_ok=True
            self.close()

    def simulate_tail(self):
        self.engine.submit('sim_signal',front_sensor=False,rear_sensor=True,rear_sensor_seen=True)
        QTimer.singleShot(150,lambda:self.engine.submit('sim_signal',capture_active=False,rear_sensor=False))

    def open_vision_window(self):
        self.vision_window.show();self.vision_window.raise_();self.vision_window.activateWindow()

    def open_grading_window(self):
        self.grading_window.show();self.grading_window.raise_();self.grading_window.activateWindow()

    def closeEvent(self,event: QCloseEvent):
        self.grading_window.shutdown()
        if self.closed_ok or not self.engine.is_alive():
            self.vision_window.hide()
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.closing=True
            self.centralWidget().setEnabled(False)
            self.vision_window.setEnabled(False)
            self.banner.setText("正在结束采集并等待图片保存完成，请稍候…")
            self.engine.submit("shutdown")
