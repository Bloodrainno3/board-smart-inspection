"""Operator controls for the camera-side SDK, separate from distance calibration."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QWidget, QLabel, QPushButton, QComboBox, QSpinBox,
    QDoubleSpinBox, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QScrollArea,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QCheckBox)
from .cl_config import LIGHTS, LABELS


class CameraCalibrationPanel(QWidget):
    def __init__(self, submit):
        super().__init__()
        self.submit = submit
        self.state = {}
        self.snapshot = None
        self.inflight = False
        self.fresh = False
        self.preview_received = False
        self.last_path = None
        self.buttons = {}
        layout = QVBoxLayout(self)
        note = QLabel('相机端灯光与明暗场校正。连接时只读取参数；调整后会回读核对。\n'
                      '先关闭厂家配置软件；控制端口与采集卡必须连接同一相机。与板缝距离标定分别设置。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.notice = QLabel('尚未连接相机控制端口')
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.notice)
        split = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(split, 1)
        left = QWidget()
        stack = QVBoxLayout(left)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        split.addWidget(scroll)
        link = QGroupBox('1  连接相机控制口')
        form = QFormLayout(link)
        self.ports = QComboBox()
        self.ports.addItem('先搜索，再选择相机端口', None)
        form.addRow('相机控制端口', self.ports)
        self.row(form, ('搜索端口', 'ports', {}), ('连接并读取', 'connect', self.port_args))
        self.row(form, ('重新读取', 'read', {}), ('断开控制口', 'disconnect', {}))
        stack.addWidget(link)
        lights = QGroupBox('2  灯光与响应参数')
        form = QFormLayout(lights)
        self.fields = {}
        for key in (*LIGHTS, 'Gain', 'Offset', 'FFC_Algorithm'):
            field = QDoubleSpinBox() if key == 'Gain' else QSpinBox()
            if key == 'Gain':
                field.setRange(1, 20); field.setDecimals(3); field.setSingleStep(.1)
            else:
                field.setRange(0, 10000 if key in LIGHTS else 255)
            self.fields[key] = field
            form.addRow(LABELS[key], field)
        self.row(form, ('应用RGB灯光', 'apply_rgb', lambda: {'changes': {k: self.fields[k].value() for k in LIGHTS}}),)
        self.row(form, ('应用增益/偏移', 'apply_response', lambda: {'changes': {k: self.fields[k].value() for k in ('Gain', 'Offset')}}),)
        self.row(form, ('应用校正算法', 'apply_algorithm', lambda: {'changes': {'FFC_Algorithm': self.fields['FFC_Algorithm'].value()}}),)
        hint = QLabel('算法：0=Basic均值；1～255=Vendor定值。\n灯光数值沿用相机回读量程；连接不会套用示例值。')
        hint.setWordWrap(True); form.addRow(hint)
        stack.addWidget(lights)
        ffc = QGroupBox('3  校正流程')
        form = QFormLayout(ffc)
        self.row(form, ('启动校正预览', 'preview_start', {}), ('停止校正预览', 'preview_stop', {}))
        self.row(form, ('停用平场校正', 'ffc_off', {'changes': {'FFC_Enabled': 0}}),
                 ('启用平场校正', 'ffc_on', {'changes': {'FFC_Enabled': 1}}))
        self.row(form, ('关RGB并记住当前值', 'dark_lights', {}), ('恢复关灯前RGB', 'restore_lights', {}))
        self.dark_ready = QCheckBox('暗场：已关灯并遮挡外界光')
        self.white_ready = QCheckBox('亮场：已放好均匀白色目标，按厂家要求移动')
        form.addRow(self.dark_ready)
        self.row(form, ('生成暗场校正', 'dark', {'kind': 2}),)
        form.addRow(self.white_ready)
        self.row(form, ('生成亮场校正', 'bright', {'kind': 1}),)
        self.dark_ready.toggled.connect(self.refresh)
        self.white_ready.toggled.connect(self.refresh)
        hint = QLabel('顺序：预览收到图像 → 停用校正 → 关灯/暗场 → 恢复灯光/亮场 → 启用校正 → 保存。\n'
                      '校正需持续采图；行触发沿用现场配置。命令应答后保持目标，按厂家要求等待校正稳定。')
        hint.setWordWrap(True); form.addRow(hint)
        stack.addWidget(ffc)
        persist = QGroupBox('4  保存与核验')
        form = QFormLayout(persist)
        self.slot = QSpinBox(); self.slot.setRange(0, 8); self.slot.setValue(1)
        form.addRow('保存到用户配置集', self.slot)
        self.row(form, ('保存该用户集并设为默认', 'save', lambda: {'slot': self.slot.value()}),)
        hint = QLabel('此操作覆盖所选相机用户集。保存应答后，停止预览；相机断电重启、重新连接并读取，核对RGB和FFC状态。')
        hint.setWordWrap(True); form.addRow(hint)
        self.row(form, ('导出相机参数', 'export', {}), ('保存预览原像素PNG', 'save_preview', {}))
        self.open_folder = QPushButton('打开校正记录目录')
        self.open_folder.clicked.connect(self.open_records)
        form.addRow(self.open_folder)
        stack.addWidget(persist)
        stack.addStretch()
        right = QWidget(); right_layout = QVBoxLayout(right)
        self.image = QLabel('校正预览：只显示最近的图像条带，不生成整板文件')
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(280, 100)
        self.image.setMaximumHeight(350)
        self.image.setStyleSheet('background:#172b43;color:white;padding:8px;')
        self.image.setWordWrap(True)
        right_layout.addWidget(self.image, 2)
        self.metrics = QLabel('RGB均值与高亮像素比例将在收到图像后显示；仅供检查，不代表颜色已标定准确。')
        self.metrics.setWordWrap(True); right_layout.addWidget(self.metrics)
        self.read_at = QLabel('相机回读：未知')
        self.read_at.setWordWrap(True); right_layout.addWidget(self.read_at)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['相机回读参数', '当前值'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        right_layout.addWidget(self.table, 3)
        split.addWidget(right)
        split.setSizes([450, 700])
        self.refresh()

    def row(self, form, *items):
        widget = QWidget(); row = QHBoxLayout(widget); row.setContentsMargins(0, 0, 0, 0)
        for label, action, args in items:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, a=action, p=args: self.send(a, p))
            self.buttons[action] = button
            row.addWidget(button)
        form.addRow(widget)

    def port_args(self):
        return {'port': self.ports.currentData()}

    def send(self, action, args):
        args = args() if callable(args) else args
        if action == 'connect' and args.get('port') is None:
            self.notice.setText('请先搜索并选择相机控制端口（不要选择PLC串口）')
            return
        action = {'apply_rgb': 'apply', 'apply_response': 'apply', 'apply_algorithm': 'apply',
                  'ffc_on': 'apply', 'ffc_off': 'apply', 'dark': 'generate', 'bright': 'generate'}.get(action, action)
        self.inflight = True
        if action == 'preview_start':
            self.preview_received = False
        self.notice.setText('正在执行相机控制操作，请等待厂家SDK应答…')
        self.refresh()
        self.submit('cl_' + action, **args)

    def update_state(self, state):
        self.state = state
        if not state.get('cl_preview'):
            self.preview_received = False
        self.refresh()

    def refresh(self, *_):
        s = self.state
        idle = not any(s.get(k) for k in ('auto', 'capture', 'saving', 'plc_command_busy', 'simulation')) and not self.inflight
        connected = s.get('cl_connected', False)
        preview = s.get('cl_preview', False)
        ready = idle and connected and self.fresh
        v = self.snapshot.get('values', {}) if self.snapshot else {}
        for key, field in self.fields.items():
            field.setEnabled(ready and key in v)
        for key, button in self.buttons.items():
            button.setEnabled(ready)
        for key in ('ports', 'connect'):
            self.buttons[key].setEnabled(idle and not preview)
        self.ports.setEnabled(idle and not preview)
        self.buttons['read'].setEnabled(idle and connected)
        self.buttons['disconnect'].setEnabled(idle and connected)
        self.buttons['preview_start'].setEnabled(ready and s.get('camera', False) and not preview)
        self.buttons['preview_stop'].setEnabled(idle and preview)
        for key, required in {'apply_rgb': LIGHTS, 'apply_response': ('Gain', 'Offset'),
            'apply_algorithm': ('FFC_Algorithm',), 'ffc_on': ('FFC_Enabled',),
            'ffc_off': ('FFC_Enabled',), 'dark_lights': LIGHTS, 'restore_lights': LIGHTS}.items():
            self.buttons[key].setEnabled(ready and all(k in v for k in required))
        for key, checkbox in (('dark', self.dark_ready), ('bright', self.white_ready)):
            self.buttons[key].setEnabled(ready and preview and self.preview_received and checkbox.isChecked() and v.get('FFC_Enabled') == 0)
            checkbox.setEnabled(ready)
        self.buttons['save_preview'].setEnabled(idle and preview and self.preview_received)
        self.open_folder.setEnabled(bool(self.last_path))
        self.slot.setEnabled(ready)

    def handle_event(self, kind, data):
        if kind == 'cl_ports':
            old = self.ports.currentData()
            self.ports.clear(); self.ports.addItem('请选择相机控制端口', None)
            for port in data:
                self.ports.addItem(port['vendor'] + ' / ' + port['identifier'], port)
            for i in range(1, self.ports.count()):
                if self.ports.itemData(i) == old:
                    self.ports.setCurrentIndex(i)
        elif kind == 'cl_snapshot':
            self.snapshot = data; self.fresh = True
            self.state['cl_connected'] = True
            self.read_at.setText('最后回读：' + data['read_at'] + '\n自动采图时不轮询校正参数。')
            v = data['values']
            for key, field in self.fields.items():
                if key in LIGHTS:
                    field.setMaximum(100 if v.get('LightValueScale') == 100 else 10000)
                if key in v:
                    field.setValue(v[key])
            self.table.setRowCount(len(data['properties']))
            for row, prop in enumerate(data['properties']):
                label = LABELS.get(prop['name'], prop['name'])
                if prop['extra']:
                    label += f" [{prop['extra']}]"
                self.table.setItem(row, 0, QTableWidgetItem(label))
                self.table.setItem(row, 1, QTableWidgetItem(str(prop['value'])))
        elif kind in ('cl_connection', 'cl_stale'):
            self.fresh = False
            self.read_at.setText('参数未回读成功或控制口已断开；表格仅保留上次记录')
            if kind == 'cl_connection':
                self.state['cl_connected'] = data['connected']
        elif kind == 'cl_done':
            self.inflight = False
            self.notice.setText(data['message'])
            self.notice.setStyleSheet('color:#135f39;' if data['ok'] else 'color:#a02821;')
        elif kind == 'cl_notice':
            self.notice.setText(data)
        elif kind == 'cl_preview':
            self.preview_received = True
            pixmap = QPixmap(); pixmap.loadFromData(data['png'])
            self.image.setPixmap(pixmap.scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.metrics.setText(f"原像素条带：{data['width']}×{data['height']}；已接收{data['lines']}行\n"
                f"RGB均值：{data['means']}；各通道≥254像素比例：{data['clipping']}%\n预览未额外调色；保存PNG保留条带原像素。")
        elif kind == 'cl_audit':
            self.last_path = data
        self.refresh()

    def open_records(self):
        if self.last_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.last_path).parent)))
