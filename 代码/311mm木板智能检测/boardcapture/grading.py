"""Manual veneer color grading. Numerical libraries are child-process only."""
from __future__ import annotations
import csv
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from .config import ASSETS, atomic_json

MATERIALS = [('hongxiang','红橡'),('ouxiang','欧橡'),('semu','色木'),('shanhetao','山核桃')]
EXTENSIONS = {'.jpg','.jpeg','.png','.bmp','.tif','.tiff','.webp'}

def model_manifest():
    return json.loads((ASSETS/'models'/'grading'/'manifest.json').read_text(encoding='utf-8'))

def select_images(directory, output_directory=None):
    root=Path(directory).resolve()
    output=Path(output_directory).resolve() if output_directory else None
    return sorted(str(p) for p in root.rglob('*') if p.is_file() and p.suffix.lower() in EXTENSIONS
                  and (output is None or (p.resolve()!=output and output not in p.resolve().parents)))

def create_request(files, material, output, capture_directory, threads=2):
    if material not in dict(MATERIALS):raise ValueError('请选择木皮种类')
    paths=list(dict.fromkeys(str(Path(p).resolve()) for p in files))
    if not paths:raise ValueError('请先选择图片')
    out=Path(output).resolve();capture=Path(capture_directory).resolve()
    if out==capture or capture in out.parents:raise ValueError('分级结果目录必须在采图目录之外')
    for path in paths:
        image=Path(path)
        if not image.is_file():raise ValueError('图片不存在：'+path)
        if image.suffix.lower() not in EXTENSIONS:raise ValueError('不支持的图片：'+path)
        if out==image.parent or image.parent in out.parents:
            raise ValueError('请将分级结果保存在所选原图目录之外')
    if not isinstance(threads,int) or not 1<=threads<=4:raise ValueError('分级线程数必须为1～4')
    entry=model_manifest()['materials'][material]
    batch=out/(time.strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:8]);batch.mkdir(parents=True)
    req={'kind':'veneer_grading','version':1,'material':material,'model_sha256':entry['onnx_sha256'],
         'files':paths,'threads':threads,'created_at':time.strftime('%Y-%m-%d %H:%M:%S')}
    atomic_json(batch/'request.json',req)
    atomic_json(batch/'progress.json',{'status':'ready','total':len(paths),'completed':0,'errors':0})
    return batch/'request.json'

def preprocess(image):
    import numpy as np
    from PIL import Image
    pixels=np.asarray(image.convert('RGB').resize((224,224),Image.Resampling.BILINEAR),dtype=np.uint8)
    x=np.ascontiguousarray(pixels.transpose(2,0,1)).astype(np.float32)/np.float32(255)
    x=(x-np.array([.485,.456,.406],np.float32)[:,None,None])/np.array([.229,.224,.225],np.float32)[:,None,None]
    return np.ascontiguousarray(x[None])

class Predictor:
    def __init__(self, material, threads=2, expected_hash=None):
        import onnxruntime as ort
        self.entry=model_manifest()['materials'][material];self.material=material
        path=ASSETS/'models'/'grading'/self.entry['file']
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=self.entry['onnx_sha256'] or (expected_hash and actual!=expected_hash):
            raise ValueError('分级模型校验不一致，请使用完整发布包；旧任务不能换模型继续')
        opts=ort.SessionOptions();opts.intra_op_num_threads=max(1,min(4,int(threads)));opts.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(path),sess_options=opts,providers=['CPUExecutionProvider'])

    def predict(self, source):
        import numpy as np
        from PIL import Image
        started=time.perf_counter();path=Path(source);before=path.stat()
        # Line-scan originals exceed Pillow's default decompression-bomb threshold.
        Image.MAX_IMAGE_PIXELS=None
        with Image.open(path) as image:
            size=image.size;x=preprocess(image)
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ValueError('图片仍在写入，请保存完成后重新分级')
        logits=self.session.run(None,{'image':x})[0][0]
        if logits.shape!=(len(self.entry['classes']),) or not np.all(np.isfinite(logits)):
            raise ValueError('模型输出无效')
        exp=np.exp(logits-logits.max());probabilities=exp/exp.sum();index=int(probabilities.argmax())
        return {'status':'done','source':str(path.resolve()),'filename':path.name,
                'material':self.entry['name'],'material_key':self.material,
                'class_id':self.entry['classes'][index],'grade':self.entry['labels'][index],
                'confidence':float(probabilities[index]),'probabilities':[
                    {'class_id':c,'label':l,'probability':float(p)}
                    for c,l,p in zip(self.entry['classes'],self.entry['labels'],probabilities)],
                'image_width':size[0],'image_height':size[1],
                'source_size':before.st_size,'source_mtime_ns':before.st_mtime_ns,
                'model_sha256':self.entry['onnx_sha256'],'processing_seconds':time.perf_counter()-started,
                'note':'颜色分组预测；置信度是模型分数，不是准确率。色木仅区分ABCD组/E组。'}

def load_records(batch):
    records=[]
    for path in sorted((Path(batch)/'items').glob('*.json')):
        records.append(json.loads(path.read_text(encoding='utf-8')))
    return records

def export_csv(batch, records):
    path=Path(batch)/'分级汇总.csv';temp=path.with_name('.summary-'+uuid.uuid4().hex+'.tmp')
    def safe(value):
        s=str(value)
        return "'"+s if s.startswith(('=','+','-','@','\t','\r')) else s
    try:
        with temp.open('x',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f)
            writer.writerow(['序号','原图路径','木皮种类','预测类别','模型置信度','状态','错误信息','各类别分数'])
            for r in records:
                writer.writerow([r['index'],safe(r['source']),r.get('material',''),r.get('grade',''),
                    f"{r['confidence']:.8f}" if 'confidence' in r else '',r['status'],safe(r.get('error','')),
                    '；'.join(f"{p['label']}={p['probability']:.8f}" for p in r.get('probabilities',[]))])
            f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
    return path

def run_worker(request_path):
    if os.name=='nt':
        import ctypes
        # Leave CPU scheduling priority to acquisition while grading runs.
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000)
    request_path=Path(request_path).resolve();batch=request_path.parent
    lock=batch/'worker.lock';lock.parent.mkdir(parents=True,exist_ok=True)
    # OS lock prevents two windows/processes from writing the same resumed batch.
    with lock.open('a+b') as handle:
        if handle.tell()==0:handle.write(b'0');handle.flush()
        if os.name=='nt':
            import msvcrt
            handle.seek(0)
            try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:return 2
        try:return _run_worker(request_path)
        finally:
            if os.name=='nt':handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)

def _run_worker(request_path):
    batch=request_path.parent;records=[];total=0
    try:
        req=json.loads(request_path.read_text(encoding='utf-8'))
        if req.get('kind')!='veneer_grading' or req.get('version')!=1:raise ValueError('不是分级任务文件')
        files=req['files'];total=len(files)
        if not files or any(not isinstance(p,str) for p in files):raise ValueError('任务图片列表无效')
        progress=lambda status,**extra:atomic_json(batch/'progress.json',dict(status=status,total=total,
            completed=len(records),errors=sum(r['status']=='error' for r in records),**extra))
        progress('running',current='正在加载模型')
        predictor=Predictor(req['material'],req.get('threads',2),req['model_sha256'])
        for index,source in enumerate(files,1):
            if (batch/'cancel.request').exists():
                records=load_records(batch)
                export_csv(batch,records);progress('paused');return 0
            output=batch/'items'/f'{index:06d}.json'
            previous=None
            if output.exists():
                try:previous=json.loads(output.read_text(encoding='utf-8'))
                except ValueError:pass
            unchanged=False
            if previous and previous.get('status')=='done' and previous.get('model_sha256')==req['model_sha256'] and previous.get('source')==source:
                try:
                    stat=Path(source).stat()
                    unchanged=(stat.st_size,stat.st_mtime_ns)==(previous['source_size'],previous['source_mtime_ns'])
                except (OSError,KeyError):pass
            if unchanged:records.append(previous);continue
            progress('running',current=source)
            try:result=predictor.predict(source)
            except Exception as error:
                result={'status':'error','source':source,'filename':Path(source).name,
                        'material':predictor.entry['name'],'error':str(error)}
            result['index']=index;atomic_json(output,result);records.append(result)
            progress('running',current=source)
        export_csv(batch,records);progress('done');return 0
    except Exception as error:
        import traceback
        atomic_json(batch/'progress.json',{'status':'error','total':total,'completed':len(records),
                                         'error':str(error),'traceback':traceback.format_exc()})
        return 1
