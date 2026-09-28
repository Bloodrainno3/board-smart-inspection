"""Recognition and metrology UI; no numerical inference on the Qt thread."""
from __future__ import annotations
import copy,json,threading
from pathlib import Path
from PySide6.QtCore import Qt,QUrl,QRectF,Signal,Slot
from PySide6.QtGui import QPixmap,QDesktopServices,QPainter
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QLineEdit,QCheckBox,
    QDoubleSpinBox,QSpinBox,QComboBox,QFileDialog,QTableWidget,QTableWidgetItem,QHeaderView,QSplitter,QScrollArea,
    QGraphicsView,QGraphicsScene,QAbstractItemView,QGroupBox,QMessageBox)
from .config import ROOT,atomic_json
from .result_io import load_result_file,export_problem_sample


class ImageView(QGraphicsView):
    def __init__(self):
        self.auto_fit=True;self.fit_rect=QRectF()
        super().__init__();self.setScene(QGraphicsScene(self));self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)
    def load(self,path):
        pixmap=QPixmap(str(path))
        if pixmap.isNull():return
        self.scene().clear();self.scene().addPixmap(pixmap);self.scene().setSceneRect(0,0,pixmap.width(),pixmap.height());self.fit()
    def fit(self):self.fit_region(self.sceneRect())
    def fit_region(self,rect):
        self.fit_rect=QRectF(rect);self.auto_fit=True
        if not self.fit_rect.isEmpty():self.fitInView(self.fit_rect,Qt.AspectRatioMode.KeepAspectRatio)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.auto_fit and not self.fit_rect.isEmpty():self.fitInView(self.fit_rect,Qt.AspectRatioMode.KeepAspectRatio)
    def wheelEvent(self,event):
        self.auto_fit=False
        factor=1.2 if event.angleDelta().y()>0 else 1/1.2
        self.scale(factor,factor);event.accept()


class VisionPanel(QWidget):
    sample_export_finished=Signal(str,str)
    def __init__(self,store,queue,notify):
        super().__init__();self.store=store;self.queue=queue;self.notify=notify;self.results=[];self.row_seams=[];self.pending_profile=None
        self.settings=store.snapshot()['vision'];self.profile=copy.deepcopy(self.settings.get('calibration'))
        layout=QVBoxLayout(self)
        self.sample_export_finished.connect(self.finish_sample_export)
        self.status=QLabel('识别就绪 · 原图保存后自动排队 · 模型：UNet++ / ResNet34')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        split=QSplitter();layout.addWidget(split,1)
        controls=QWidget();form=QFormLayout(controls)
        self.enabled=QCheckBox('保存完整板图后自动识别');self.enabled.setChecked(self.settings['enabled'])
        form.addRow(self.enabled)
        self.material=QComboBox();self.material.addItems(['混合板材','欧橡','色木','山核桃']);self.material.setCurrentText(self.settings['material'])
        form.addRow('材种记录',self.material)
        note=QLabel('三个材种共用同一模型；此项用于记录，模型输出为板缝/背景。');note.setWordWrap(True);form.addRow(note)
        self.threshold=QDoubleSpinBox();self.threshold.setRange(.01,.99);self.threshold.setSingleStep(.05);self.threshold.setValue(self.settings['threshold'])
        self.coverage=QDoubleSpinBox();self.coverage.setDecimals(3);self.coverage.setRange(.001,1);self.coverage.setValue(self.settings['min_row_coverage'])
        self.maximum=QSpinBox();self.maximum.setRange(1,20);self.maximum.setValue(self.settings['max_seams'])
        self.threads=QSpinBox();self.threads.setRange(1,8);self.threads.setValue(self.settings['threads'])
        form.addRow('分割阈值',self.threshold);form.addRow('最小横向覆盖率',self.coverage);form.addRow('每板最多缝数',self.maximum);form.addRow('识别CPU线程',self.threads)
        self.output=QLineEdit(self.settings['results_directory'])
        form.addRow('结果目录',self.output);form.addRow(self.button('选择结果目录',self.choose_output))
        form.addRow(self.button('应用识别设置',self.apply))
        form.addRow(self.button('导入图片识别（可多选）',self.import_images))
        form.addRow(self.button('导入目录（含子目录）',self.import_directory))
        form.addRow(self.button('重新排队失败任务',self.queue.retry_errors))
        cal=QGroupBox('现场距离标定');cf=QFormLayout(cal)
        self.square=QDoubleSpinBox();self.square.setRange(0,100);self.square.setDecimals(4);self.square.setSuffix(' mm');self.square.setSpecialValueText('0：按A4外框210×297 mm')
        cf.addRow('实测单格边长',self.square)
        cf.addRow(self.button('选择标定照片并计算',self.calibrate))
        self.cal_status=QLabel();self.cal_status.setWordWrap(True);cf.addRow(self.cal_status)
        self.apply_cal=self.button('将当前计算结果应用于后续板图',self.apply_calibration);self.apply_cal.setEnabled(False);cf.addRow(self.apply_cal)
        cf.addRow(self.button('导入已有标定配置',self.import_calibration))
        cf.addRow(self.button('导出已应用标定配置',self.export_calibration))
        cf.addRow(self.button('清除标定（仅输出像素）',self.clear_calibration))
        explain=QLabel('打印模板为7×7方格、6×6内角点。使用完整A4纸且按实际打印尺寸；也可输入尺量的单格边长。相机DPI、编码器倍率或走纸速度变化后需重新标定。')
        explain.setWordWrap(True);cf.addRow(explain);form.addRow(cal)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(controls);split.addWidget(scroll)
        right=QWidget();rv=QVBoxLayout(right)
        bar=QHBoxLayout();bar.addWidget(self.button('适应视图',lambda:self.view.fit()));bar.addWidget(self.button('打开当前结果目录',self.open_current));bar.addWidget(self.button('加载已有结果',self.load_result));rv.addLayout(bar)
        self.export_sample_button=self.button('导出问题样本（含相机原图、结果和掩膜）',self.export_sample)
        rv.addWidget(self.export_sample_button)
        self.view_mode=QComboBox()
        for label,key in [('测量结果（四角与中心）','overlay'),('模型原始掩膜（白色=模型直接预测）','model_mask'),('测量区域（含上下边界配对补全）','measurement_mask'),('相机原图缩略图','source_preview')]:
            self.view_mode.addItem(label,key)
        self.view_mode.currentIndexChanged.connect(lambda _:self.select_result(self.table.currentRow(),0))
        rv.addWidget(self.view_mode)
        self.view=ImageView();rv.addWidget(self.view,1)
        hint=QLabel('滚轮缩放，按住拖动。红色：测量区域；黄色：四角；绿色：对角线；紫色：目标交点。宽缝配对补全会标为待复核；模型原始掩膜单独保留。')
        hint.setWordWrap(True);rv.addWidget(hint)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['板图 / 缝','X 像素','Y 像素','板顶Y','纵向像素','纵向 mm','状态'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True);self.table.setMaximumHeight(230)
        self.table.cellClicked.connect(self.select_result);rv.addWidget(self.table)
        self.details=QLabel('尚无识别结果。距离零点：目标交点同一X位置的木板上端边线。');self.details.setWordWrap(True);self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);rv.addWidget(self.details)
        split.addWidget(right);split.setSizes([370,900]);self.refresh_calibration()

    def button(self,text,callback):
        b=QPushButton(text);b.clicked.connect(callback);return b
    def fail(self,e):self.status.setText('需要处理：'+str(e));self.notify(str(e),'error')
    def current_settings(self):
        v=copy.deepcopy(self.store.snapshot()['vision']);v.update(enabled=self.enabled.isChecked(),material=self.material.currentText(),threshold=self.threshold.value(),
            min_row_coverage=self.coverage.value(),max_seams=self.maximum.value(),threads=self.threads.value(),results_directory=self.output.text().strip(),calibration=self.profile)
        return v
    def apply(self):
        try:self.store.save_vision(self.current_settings());self.status.setText('识别设置已应用，排队中的板图保留入队时的设置与标定')
        except Exception as e:self.fail(e)
    def choose_output(self):
        p=QFileDialog.getExistingDirectory(self,'选择识别结果目录',self.output.text())
        if p:self.output.setText(p)
    def enqueue_files(self,files):
        try:
            v=self.current_settings();self.store.save_vision(v)
            for path in files:self.queue.enqueue(path,v)
            self.status.setText(f'已加入 {len(files)} 张图片；采集可同时运行')
        except Exception as e:self.fail(e)
    def import_images(self):
        files,_=QFileDialog.getOpenFileNames(self,'选择板材图片','','图像 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff)')
        if files:self.enqueue_files(files)
    def import_directory(self):
        directory=QFileDialog.getExistingDirectory(self,'选择板材目录')
        if directory:
            files=[str(p) for p in Path(directory).rglob('*') if p.suffix.lower() in ('.jpg','.jpeg','.png','.bmp','.tif','.tiff') and not any(x in p.parts for x in ('标定','真实标定'))]
            self.enqueue_files(files)
    def calibrate(self):
        path,_=QFileDialog.getOpenFileName(self,'选择完整A4标定纸照片','','图像 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff)')
        if path:
            try:self.queue.calibration_job(path,self.square.value());self.cal_status.setText('标定计算已排队…')
            except Exception as e:self.fail(e)
    def refresh_calibration(self):
        p=self.profile
        self.cal_status.setText('当前未标定，只输出像素距离' if not p else f"已应用标定 {p['id'][:8]}\nX {p['mm_per_pixel_x']:.8f} mm/px\nY {p['mm_per_pixel_y']:.8f} mm/px\n适用图宽 {p['image_width']} px")
    def apply_calibration(self):
        if self.pending_profile:
            self.profile=copy.deepcopy(self.pending_profile);self.apply();self.refresh_calibration()
    def import_calibration(self):
        path,_=QFileDialog.getOpenFileName(self,'导入标定配置','','JSON (*.json)')
        if path:
            try:
                p=json.loads(Path(path).read_text(encoding='utf-8-sig'))
                # Pure validation here, without loading NumPy/OpenCV into acquisition UI.
                if not p.get('id') or not p.get('image_width') or min(p.get('mm_per_pixel_x',0),p.get('mm_per_pixel_y',0))<=0 or p.get('warnings'):
                    raise ValueError('标定配置无效或一致性检查未通过')
                self.pending_profile=p;self.apply_calibration()
            except Exception as e:self.fail(e)
    def export_calibration(self):
        if not self.profile:self.fail('尚未应用标定');return
        path,_=QFileDialog.getSaveFileName(self,'导出标定配置','现场标定.json','JSON (*.json)')
        if path:
            try:atomic_json(path,self.profile)
            except Exception as e:self.fail(e)
    def clear_calibration(self):
        self.profile=None;self.pending_profile=None;self.apply_cal.setEnabled(False);self.apply();self.refresh_calibration()
    def load_result(self):
        path,_=QFileDialog.getOpenFileName(self,'加载识别结果',self.output.text(),'JSON (*.json)')
        if path:
            try:self.add_result(load_result_file(path))
            except Exception as e:self.fail(e)
    def export_sample(self):
        row=self.table.currentRow()
        if not 0<=row<len(self.results):self.fail('请先选中一条识别结果');return
        path,_=QFileDialog.getSaveFileName(self,'导出所选问题样本（包含相机原图）','板缝问题样本.zip','ZIP (*.zip)')
        if not path:return
        if not path.lower().endswith('.zip'):path+='.zip'
        result=copy.deepcopy(self.results[row]);self.export_sample_button.setEnabled(False)
        self.status.setText('正在导出所选样本，采集和识别可继续运行…')
        def run():
            error=''
            try:export_problem_sample(result,path)
            except Exception as e:error=str(e)
            try:self.sample_export_finished.emit(path,error)
            except RuntimeError:pass  # Window closed during local export.
        threading.Thread(target=run,name='ProblemSampleExport',daemon=True).start()
    @Slot(str,str)
    def finish_sample_export(self,path,error):
        self.export_sample_button.setEnabled(True)
        if error:self.fail(error)
        else:
            self.status.setText('问题样本已导出（含原图）：'+path)
            self.notify('问题样本已导出（含原图）：'+path)
            QMessageBox.information(self,'问题样本已导出','已保存原图、结果和可用掩膜：\n'+path)
    def add_result(self,result):
        if 'seams' not in result:raise ValueError('请选择result.json识别结果文件')
        records=result['seams'] or [None]
        for seam in records:
            row=self.table.rowCount();self.table.insertRow(row);self.results.append(result);self.row_seams.append(seam)
            def f(x):return '—' if x is None else f'{x:.2f}'
            vals=[result['board_id'][:12]+(f" / {seam['index']}" if seam else ' / 未检出')]
            vals+=([f(seam['center_px'][0]),f(seam['center_px'][1]),f(seam['top_at_center_x_px']),f(seam['distance_from_board_top_px']),f(seam['distance_from_board_top_mm'])] if seam else ['—']*5)
            vals+=['宽缝补全·待复核' if seam and seam.get('boundary_pair_completed') else ('已换算' if result['status']=='measured' else '待复核')]
            for col,val in enumerate(vals):self.table.setItem(row,col,QTableWidgetItem(val))
        # Bound GUI history; all historical results remain on disk.
        while self.table.rowCount()>500:self.table.removeRow(0);self.results.pop(0);self.row_seams.pop(0)
        self.table.selectRow(self.table.rowCount()-1);self.select_result(self.table.rowCount()-1,0)
    def select_result(self,row,col):
        if not 0<=row<len(self.results):return
        r=self.results[row]
        for i in range(self.view_mode.count()):
            self.view_mode.model().item(i).setEnabled(bool(r.get(self.view_mode.itemData(i))))
        key=self.view_mode.currentData()
        if not r.get(key):
            self.view_mode.setCurrentIndex(0);return
        self.view.load(r[key])
        seam=self.row_seams[row]
        if seam:
            scale=self.view.sceneRect().height()/r['image_height']
            cy=seam['center_px'][1]*scale
            corners=seam.get('corners_px',[[0,seam['center_px'][1]]])
            lo=max(0,min(cy-250,min(c[1] for c in corners)*scale-70))
            hi=min(self.view.sceneRect().height(),max(cy+250,max(c[1] for c in corners)*scale+70))
            self.view.fit_region(QRectF(0,lo,self.view.sceneRect().width(),max(1,hi-lo)))
        d=r.get('detection_diagnostics')
        diagnostic=(f"{d['explanation']}\n模型预测 {d['model_threshold_pixels']} 像素 → 首尾排除后 {d['after_edge_margin_pixels']} 像素 → 行候选 {d['row_candidate_count']} 组 → 保留 {d['seam_count']} 个缝区\n" if d else '')
        if d and 'region_selection' in d:
            diagnostic=f"{d['explanation']}\n模型预测 {d['model_threshold_pixels']} 像素 → 横向候选 {d['region_selection']['projected_candidate_count']} 组 → 保留 {d['seam_count']} 个缝区（不再按单行覆盖筛选）\n"
        if not r['seam_count']:diagnostic+='本次未检出，不代表板材没有缝；可切换模型原始掩膜或导出问题样本。\n'
        warnings=[w for w in r.get('warnings',[]) if not d or w!=d['explanation']]
        self.details.setText(f"{r['board_id']} · {r['seam_count']} 个候选缝区 · 用时 {r['processing_seconds']:.1f}s\n"+diagnostic+'；'.join(warnings)+'\n'+r['result_file'])
    def open_current(self):
        row=self.table.currentRow();path=Path(self.results[row]['result_file']).parent if 0<=row<len(self.results) else Path(self.output.text())
        path.mkdir(parents=True,exist_ok=True);QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    def handle_event(self,kind,data):
        if kind=='vision_state':self.status.setText(f"识别队列：等待 {data['queued']} · 运行 {data['running']} · 完成 {data['done']} · 失败 {data['error']}")
        elif kind=='vision_progress':self.status.setText(f"正在识别 {Path(data['file']).name} · 切片 {data['tile']}/{data['total']} · 采集可同时运行")
        elif kind=='vision_result':self.add_result(data)
        elif kind=='vision_error':self.fail(data)
        elif kind=='vision_notice':self.notify(data)
        elif kind=='calibration_result':
            self.pending_profile=data;self.apply_cal.setEnabled(not bool(data.get('warnings')))
            self.view.load(data['preview']);self.cal_status.setText(f"计算完成，尚未应用\nX {data['mm_per_pixel_x']:.8f} mm/px\nY {data['mm_per_pixel_y']:.8f} mm/px\n方格一致性偏差 {data['square_axis_mismatch']:.2%}\n"+'；'.join(data.get('warnings',[])))
