"""Independent manual grading window; a child process owns inference."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from PySide6.QtCore import Qt, QProcess, QTimer, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,
    QPushButton,QComboBox,QLineEdit,QSpinBox,QFileDialog,QTableWidget,QTableWidgetItem,
    QAbstractItemView,QHeaderView,QSplitter,QMessageBox)
from .config import ROOT, atomic_json
from .grading import MATERIALS, model_manifest, select_images, create_request, load_records, export_csv

class GradingWindow(QMainWindow):
    def __init__(self, store, notify, parent):
        super().__init__(parent,Qt.WindowType.Window)
        self.store=store;self.notify=notify;self.pending=[];self.records=[];self.batch=None;self.seen=0
        self.preferences=store.path.parent/'grading.json'
        prefs={}
        try:prefs=json.loads(self.preferences.read_text(encoding='utf-8'))
        except (OSError,ValueError):pass
        self.setWindowTitle('木皮颜色分级 · 手动任务窗口')
        rect=parent.screen().availableGeometry();self.resize(min(1200,rect.width()-30),min(800,rect.height()-40))
        self.process=QProcess(self);self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.timer=QTimer(self);self.timer.setInterval(400);self.timer.timeout.connect(self.poll)
        central=QWidget();self.setCentralWidget(central);layout=QVBoxLayout(central)
        note=QLabel('选择木皮种类和图片后，点击“开始分级”。本窗口仅手动启动；自动分级请在主界面选择联动模式。')
        note.setWordWrap(True);layout.addWidget(note)
        form=QFormLayout();layout.addLayout(form)
        self.material=QComboBox()
        for key,name in MATERIALS:self.material.addItem(name,key)
        idx=self.material.findData(prefs.get('material','hongxiang'));self.material.setCurrentIndex(max(0,idx))
        form.addRow('木皮种类',self.material)
        self.classes=QLabel();form.addRow('分类范围',self.classes)
        self.material.currentIndexChanged.connect(self.update_classes);self.update_classes()
        self.output=QLineEdit(prefs.get('output',str(ROOT/'grading_results')))
        row=QHBoxLayout();row.addWidget(self.output);self.choose_output_button=self.button('选择结果目录',self.choose_output);row.addWidget(self.choose_output_button)
        form.addRow('分级结果目录',row)
        self.threads=QSpinBox();self.threads.setRange(1,4);self.threads.setValue(prefs.get('threads',2));form.addRow('分级CPU线程',self.threads)
        bar=QHBoxLayout();layout.addLayout(bar)
        self.images_button=self.button('选择图片（可多选）',self.choose_images);bar.addWidget(self.images_button)
        self.folder_button=self.button('选择文件夹（含子目录）',self.choose_folder);bar.addWidget(self.folder_button)
        self.start_button=self.button('开始分级',self.start_selected);bar.addWidget(self.start_button)
        self.pause_button=self.button('暂停分级',self.pause);bar.addWidget(self.pause_button);self.pause_button.setEnabled(False)
        self.resume_button=self.button('打开 / 继续已有任务',self.choose_resume);bar.addWidget(self.resume_button)
        self.selection=QLabel('尚未选择图片');layout.addWidget(self.selection)
        self.status=QLabel('分级尚未启动');self.status.setWordWrap(True);layout.addWidget(self.status)
        split=QSplitter();layout.addWidget(split,1)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['图片','木皮','预测类别','模型置信度','状态'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True);self.table.cellClicked.connect(self.select_result);split.addWidget(self.table)
        right=QWidget();rv=QVBoxLayout(right);self.preview=QLabel('选中结果可查看原图缩略图');self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(220,160);rv.addWidget(self.preview,1)
        self.details=QLabel('置信度为模型分数，不等于准确率。色木模型只能区分ABCD组与E组。')
        self.details.setWordWrap(True);self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);rv.addWidget(self.details);split.addWidget(right)
        split.setSizes([700,400]);layout.addWidget(self.button('打开当前结果目录（JSON / CSV）',self.open_results))
        tail=QLabel('关闭此窗口会保留正在运行的分级；退出整个软件会暂停分级。结果单独保存，原图保持不变。')
        tail.setWordWrap(True);layout.addWidget(tail);self.set_busy(False)

    def button(self,text,callback):
        b=QPushButton(text);b.clicked.connect(callback);return b
    def update_classes(self):
        try:self.classes.setText(' / '.join(model_manifest()['materials'][self.material.currentData()]['labels']))
        except Exception as e:self.classes.setText('模型文件不完整：'+str(e))
    def error(self,error):
        self.status.setText('分级需要处理：'+str(error));self.notify('分级：'+str(error),'error')
    def select_files(self,files):
        self.pending=list(dict.fromkeys(files));self.selection.setText(f'已选择 {len(self.pending)} 张，尚未开始分级')
        self.start_button.setEnabled(bool(self.pending) and self.process.state()==QProcess.ProcessState.NotRunning)
    def choose_images(self):
        files,_=QFileDialog.getOpenFileNames(self,'选择同一种木皮的图片','','图像 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp)')
        if files:self.select_files(files)
    def choose_folder(self):
        path=QFileDialog.getExistingDirectory(self,'选择同一种木皮的图片目录')
        if path:
            try:self.select_files(select_images(path,self.output.text()))
            except Exception as e:self.error(e)
    def choose_output(self):
        path=QFileDialog.getExistingDirectory(self,'选择分级结果目录',self.output.text())
        if path:self.output.setText(path)
    def set_busy(self,busy):
        for w in [self.material,self.output,self.choose_output_button,self.threads,self.images_button,self.folder_button,self.resume_button]:w.setEnabled(not busy)
        self.start_button.setEnabled(not busy and bool(self.pending));self.pause_button.setEnabled(busy)
    def start_selected(self):
        try:
            if not self.output.text().strip():raise ValueError('请选择分级结果目录')
            request=create_request(self.pending,self.material.currentData(),self.output.text(),
                                   self.store.snapshot()['output']['directory'],self.threads.value())
            atomic_json(self.preferences,{'material':self.material.currentData(),'output':self.output.text(),'threads':self.threads.value()})
            self.launch(request);self.pending=[];self.selection.setText('所选图片已提交')
        except Exception as e:self.error(e)
    def launch(self,request):
        if self.process.state()!=QProcess.ProcessState.NotRunning:raise ValueError('已有分级任务正在运行')
        request=Path(request).resolve();req=json.loads(request.read_text(encoding='utf-8'))
        if req.get('kind')!='veneer_grading' or req.get('version')!=1:raise ValueError('请选择分级任务目录里的 request.json')
        self.batch=request.parent;(self.batch/'cancel.request').unlink(missing_ok=True)
        self.records=[];self.seen=0;self.table.setRowCount(0)
        prefix=[] if getattr(sys,'frozen',False) else [str(ROOT/'main.py')]
        self.process.setProgram(sys.executable);self.process.setArguments(prefix+['--grading-worker',str(request)])
        self.process.setWorkingDirectory(str(ROOT));self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.setStandardOutputFile(str(self.batch/'worker.log'))
        self.set_busy(True);self.status.setText('正在启动分级，自动采图可继续运行…')
        self.process.start();self.timer.start()
    def choose_resume(self):
        path,_=QFileDialog.getOpenFileName(self,'选择要继续的任务 request.json',self.output.text(),'分级任务 (request.json)')
        if path:
            try:self.launch(path)
            except Exception as e:self.error(e)
    def pause(self):
        if self.batch:
            (self.batch/'cancel.request').touch();self.pause_button.setEnabled(False)
            self.status.setText('正在完成当前图片后暂停；已完成结果会保留')
    def append_result(self,r):
        row=self.table.rowCount();self.table.insertRow(row);self.records.append(r)
        values=[r.get('filename',Path(r['source']).name),r.get('material',''),r.get('grade','—'),
                f"{r['confidence']:.2%}" if 'confidence' in r else '—','完成' if r['status']=='done' else '失败']
        for col,value in enumerate(values):
            item=QTableWidgetItem(value);item.setToolTip(r['source'] if col==0 else r.get('error',''));self.table.setItem(row,col,item)
        if self.table.rowCount()>2000:self.table.removeRow(0);self.records.pop(0)
    def poll(self):
        if not self.batch:return
        try:
            p=json.loads((self.batch/'progress.json').read_text(encoding='utf-8'))
            while self.seen<p.get('completed',0):
                path=self.batch/'items'/f'{self.seen+1:06d}.json'
                if not path.exists():break
                self.append_result(json.loads(path.read_text(encoding='utf-8')));self.seen+=1
            labels={'ready':'准备中','running':'分级中','paused':'已暂停','done':'已完成','error':'失败'}
            self.status.setText(f"{labels.get(p['status'],p['status'])}：{p.get('completed',0)}/{p.get('total',0)} 张，失败 {p.get('errors',0)} 张。"+
                                (p.get('error','') or Path(p.get('current','')).name))
        except (OSError,ValueError,KeyError):pass
    def finished(self,code,exit_status):
        self.timer.stop();self.poll();self.set_busy(False)
        try:
            p=json.loads((self.batch/'progress.json').read_text(encoding='utf-8'))
            if p.get('status') in ('running','ready'):self.mark_paused('进程已结束，可继续已有任务')
            elif code and p.get('status')!='error':self.error('分级进程退出，请查看结果目录 worker.log')
        except (OSError,ValueError):self.error('分级进程未生成结果，请查看 worker.log')
        if self.table.rowCount():self.table.selectRow(self.table.rowCount()-1);self.select_result(self.table.rowCount()-1,0)
    def process_error(self,error):
        if error==QProcess.ProcessError.FailedToStart:
            self.timer.stop();self.set_busy(False);self.error(self.process.errorString())
    def mark_paused(self,reason):
        if not self.batch:return
        records=load_records(self.batch)
        try:export_csv(self.batch,records)
        except OSError:pass  # Per-image JSON remains durable even when Excel holds CSV open.
        req=json.loads((self.batch/'request.json').read_text(encoding='utf-8'))
        atomic_json(self.batch/'progress.json',{'status':'paused','total':len(req['files']),
            'completed':len(records),'errors':sum(r['status']=='error' for r in records),'reason':reason})
        self.poll()
    def select_result(self,row,col):
        if not 0<=row<len(self.records):return
        r=self.records[row];reader=QImageReader(r['source']);size=reader.size()
        if size.isValid():size.scale(QSize(600,600),Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
        image=reader.read()
        if not image.isNull():self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        else:self.preview.setText('原图无法预览')
        self.details.setText(r['source']+'\n'+('；'.join(f"{p['label']} {p['probability']:.2%}" for p in r.get('probabilities',[])) or r.get('error','')))
    def open_results(self):
        path=self.batch or Path(self.output.text());path.mkdir(parents=True,exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def shutdown(self):
        self.timer.stop()
        if self.process.state()!=QProcess.ProcessState.NotRunning:
            self.pause();self.process.terminate()
            if not self.process.waitForFinished(1500):self.process.kill();self.process.waitForFinished(2000)
            try:self.mark_paused('退出软件后等待手动继续')
            except Exception as e:self.notify('分级结果已保留，但状态更新失败：'+str(e),'error')
        self.hide()
    def closeEvent(self,event):
        self.hide();event.ignore()
