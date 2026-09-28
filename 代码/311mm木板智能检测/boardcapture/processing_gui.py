"""Selectable per-board automatic processing and result-stream status."""
import copy
from pathlib import Path
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QComboBox,QLabel,
    QPushButton,QLineEdit,QSpinBox,QDoubleSpinBox,QCheckBox,QFileDialog,QTableWidget,QTableWidgetItem,
    QHeaderView,QAbstractItemView,QScrollArea)
from .grading import MATERIALS

MODES=[('只采图',False,False),('采图＋板缝识别',True,False),('采图＋木皮分级',False,True),('采图＋板缝＋分级',True,True)]

class ProcessingPanel(QWidget):
    changed=Signal()
    def __init__(self,store,controller,notify):
        super().__init__();self.store=store;self.controller=controller;self.notify=notify;self.rows={};self.board_ids=[];self.records={}
        cfg=store.snapshot();p=cfg['processing']
        self.quick_controls=QWidget();bar=QHBoxLayout(self.quick_controls);bar.setContentsMargins(0,0,0,0)
        bar.addWidget(QLabel('采图模式'))
        self.mode=QComboBox()
        for label,seam,grade in MODES:self.mode.addItem(label,(seam,grade))
        bar.addWidget(self.mode)
        bar.addWidget(QLabel('分级材种'));self.material=QComboBox()
        for key,name in MATERIALS:self.material.addItem(name,key)
        self.material.setCurrentIndex(max(0,self.material.findData(p['material'])));bar.addWidget(self.material)
        apply=QPushButton('应用模式');apply.clicked.connect(self.apply_mode);bar.addWidget(apply)
        self.summary=QLabel();bar.addWidget(self.summary,1)
        self.mode.setCurrentIndex(next(i for i,(_,s,g) in enumerate(MODES) if (s,g)==(cfg['vision']['enabled'],p['auto_grade'])))
        self.mode.currentIndexChanged.connect(self.preview_mode);self.preview_mode()
        layout=QVBoxLayout(self)
        note=QLabel('模式在主界面上方选择，点击“应用模式”后对随后保存的完整板图生效。已排队板图保留各自设置。\n每张板采完后后台识别，下一张板可继续采集；板缝和分级分别完成后推送，最后输出整板完成事件。')
        note.setWordWrap(True);layout.addWidget(note)
        settings=QWidget();form=QFormLayout(settings)
        self.output=QLineEdit(p['results_directory']);form.addRow('在线结果目录',self.output)
        choose=QPushButton('选择在线结果目录');choose.clicked.connect(self.choose_output);form.addRow(choose)
        self.threads=QSpinBox();self.threads.setRange(1,4);self.threads.setValue(p['grading_threads']);form.addRow('自动分级CPU线程',self.threads)
        self.confidence=QDoubleSpinBox();self.confidence.setRange(0,1);self.confidence.setSingleStep(.05);self.confidence.setValue(p['min_confidence']);form.addRow('低于此置信度标为待复核',self.confidence)
        self.enabled=QCheckBox('启用 TCP JSON 结果服务');self.enabled.setChecked(p['tcp_enabled']);form.addRow(self.enabled)
        self.host=QLineEdit(p['tcp_host']);self.port=QSpinBox();self.port.setRange(1024,65535);self.port.setValue(p['tcp_port'])
        address=QHBoxLayout();address.addWidget(self.host);address.addWidget(self.port);form.addRow('监听IP / 端口',address)
        self.token=QLineEdit(p['tcp_token']);self.token.setEchoMode(QLineEdit.EchoMode.Password);form.addRow('局域网接口口令',self.token)
        hint=QLabel('同机接收用127.0.0.1；局域网可设本机IPv4或0.0.0.0，口令至少16位。接收方需按说明订阅并ACK；断线可补传。')
        hint.setWordWrap(True);form.addRow(hint)
        save=QPushButton('应用处理与TCP设置');save.clicked.connect(self.apply_all);form.addRow(save)
        controls=QScrollArea();controls.setWidgetResizable(True);controls.setWidget(settings);controls.setMaximumHeight(360);layout.addWidget(controls)
        self.tcp=QLabel('TCP服务未启动');layout.addWidget(self.tcp)
        self.queue=QLabel('尚无在线板图');layout.addWidget(self.queue)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['板图编号','板缝距离 mm','颜色分级','置信度','处理状态','板图ID'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table,1)
        buttons=QHBoxLayout();layout.addLayout(buttons)
        for label,callback in [('打开在线结果目录',self.open_output),('继续未完成板图处理',controller.resume)]:
            b=QPushButton(label);b.clicked.connect(callback);buttons.addWidget(b)
        review=QLabel('未标定的距离输出null，未检出不代表无缝；模拟、恢复任务和待复核结果均有标记。这里输出检测数据，不向切割机或轮子发送动作命令。')
        review.setWordWrap(True);layout.addWidget(review);self.refresh_summary()
    def preview_mode(self):self.material.setEnabled(self.mode.currentData()[1])
    def refresh_summary(self):
        cfg=self.store.snapshot();flags=(cfg['vision']['enabled'],cfg['processing']['auto_grade'])
        label=next(t for t,s,g in MODES if (s,g)==flags);self.summary.setText('已生效：'+label)
    def save(self,all_settings):
        try:
            with self.store.lock:
                cfg=self.store.snapshot();p=copy.deepcopy(cfg['processing'])
                seam,grade=self.mode.currentData();cfg['vision']['enabled']=seam
                p.update(auto_grade=grade,material=self.material.currentData())
                if all_settings:p.update(results_directory=self.output.text().strip(),grading_threads=self.threads.value(),
                    min_confidence=self.confidence.value(),tcp_enabled=self.enabled.isChecked(),tcp_host=self.host.text().strip(),tcp_port=self.port.value(),tcp_token=self.token.text())
                cfg['processing']=p;self.store.save(cfg)
            self.refresh_summary();self.changed.emit();self.notify('采图联动模式已应用；已排队板图保持原设置。')
        except Exception as e:self.notify('联动设置失败：'+str(e),'error')
    def apply_mode(self):self.save(False)
    def apply_all(self):self.save(True)
    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,'选择在线结果目录',self.output.text())
        if path:self.output.setText(path)
    def open_output(self):
        path=Path(self.store.snapshot()['processing']['results_directory']);path.mkdir(parents=True,exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def handle_event(self,kind,data):
        if kind=='processing_tcp':
            self.tcp.setText(f"TCP已监听 {data['host']}:{data['port']}" if data['listening'] else 'TCP未监听 '+data.get('error',''))
        elif kind=='processing_state':
            self.queue.setText(f"在线队列：等待 {data.get('queued',0)} · 运行 {data.get('running',0)} · 待继续 {data.get('held',0)} · 完成 {data.get('done',0)} · 接收端 {data.get('tcp_clients',0)} · 事件序号 {data.get('latest_seq',0)}"+('；积压较多，结果可能延迟' if data.get('queued',0)>3 else ''))
            self.refresh_summary()
        elif kind=='processing_error':self.notify(data,'error');self.queue.setText(str(data))
        elif kind=='processing_event':
            identity=data['board_id']
            if identity not in self.records:
                self.board_ids.append(identity);self.records[identity]={'board_number':data['board_number'],'modules':{},'state':'等待处理'}
                if len(self.board_ids)>300:self.records.pop(self.board_ids.pop(0),None);self.table.removeRow(0)
                self.table.setRowCount(len(self.board_ids))
            r=self.records[identity];event=data['type']
            for module in ['seam','grading']:
                if event.startswith(module+'.'):r['modules'][module]=data['result'];r['state']='处理中'
            if event=='board.completed':r['modules']=data['results'];r['state']='待复核' if data['requires_review'] else '已完成'
            if data['simulation']:r['state']='模拟 / '+r['state']
            seam=r['modules'].get('seam',{});grade=r['modules'].get('grading',{})
            distances='—'
            if seam.get('status')=='done':
                values=seam['result'].get('seams',[])
                distances=' / '.join('未标定/无效' if s.get('distance_from_board_top_mm') is None else f"{s['distance_from_board_top_mm']:.2f}" for s in values) or '未检出'
            g=grade.get('result',{});values=[r['board_number'],distances,g.get('grade','—'),f"{g['confidence']:.2%}" if 'confidence' in g else '—',r['state'],identity]
            row=self.board_ids.index(identity)
            for col,value in enumerate(values):self.table.setItem(row,col,QTableWidgetItem(value))
