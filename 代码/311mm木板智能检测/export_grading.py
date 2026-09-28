"""Export the four supplied ResNet50 checkpoints, preserving their class order."""
from pathlib import Path
import argparse, hashlib, json, shutil
import numpy as np
import torch
import timm
import onnxruntime as ort
from PIL import Image

NAMES = {'hongxiang': '红橡', 'ouxiang': '欧橡', 'semu': '色木', 'shanhetao': '山核桃'}
LABELS = {'qian':'浅色','shen':'深色','zhong':'中色','shuangse':'双色','bai':'白色',
          'semu_abcd_cn':'ABCD类','semu_e_cn':'E类'}

def prepare(image):
    array = np.asarray(image.convert('RGB').resize((224,224),Image.Resampling.BILINEAR),dtype=np.uint8)
    x = np.ascontiguousarray(array.transpose(2,0,1)).astype(np.float32) / np.float32(255)
    return ((x-np.array([.485,.456,.406],np.float32)[:,None,None]) /
            np.array([.229,.224,.225],np.float32)[:,None,None])[None]

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--samples',required=True)
    a=p.parse_args();dest=Path(__file__).parent/'models'/'grading';dest.mkdir(parents=True,exist_ok=True)
    Image.MAX_IMAGE_PIXELS=None
    samples=[]
    for image in sorted(Path(a.samples).rglob('*.jpg')):
        if any(x in image.parts for x in ('标定','真实标定')):continue
        with Image.open(image) as im:samples.append((str(image),prepare(im)))
    if not samples:raise ValueError('Need real sample images for numerical validation')
    torch.set_num_threads(2)
    manifest={'version':1,'model_name':'resnet50.tv_in1k','input_size':224,
              'preprocessing':{'resize':'PIL BILINEAR full image, no crop','color':'RGB','divisor':255,
                               'mean':[.485,.456,.406],'std':[.229,.224,.225]},'materials':{}}
    for key,name in NAMES.items():
        folder=next(p for p in Path(a.source).iterdir() if p.name.startswith(name))
        weights=folder/'best.pt';c=torch.load(weights,map_location='cpu',weights_only=True)
        assert c['model_name']==manifest['model_name']
        classes=[k for k,v in sorted(c['class_to_idx'].items(),key=lambda kv:kv[1])]
        assert sorted(c['class_to_idx'].values())==list(range(len(classes)))
        model=timm.create_model(c['model_name'],pretrained=False,num_classes=len(classes)).eval()
        model.load_state_dict(c['model'],strict=True)
        output=dest/(key+'.onnx')
        with torch.inference_mode():
            torch.onnx.export(model,torch.from_numpy(samples[0][1]),str(output),input_names=['image'],
                              output_names=['logits'],opset_version=17,dynamo=False)
        opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
        session=ort.InferenceSession(str(output),sess_options=opts,providers=['CPUExecutionProvider'])
        checks=[]
        for filename,x in samples:
            with torch.inference_mode():expected=model(torch.from_numpy(x)).numpy()
            actual=session.run(None,{'image':x})[0]
            ep=torch.softmax(torch.from_numpy(expected),1).numpy();ap=torch.softmax(torch.from_numpy(actual),1).numpy()
            error=float(np.max(np.abs(ep-ap)))
            assert error<1e-5 and ep.argmax()==ap.argmax(),(name,filename,error)
            checks.append({'image':filename,'max_probability_error':error,'class':classes[int(ep.argmax())]})
        manifest['materials'][key]={'name':name,'file':output.name,'classes':classes,
            'labels':[LABELS[k] for k in classes],'checkpoint_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),
            'onnx_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'epoch':c['epoch'],
            'training_validation_metrics':c['val_metrics'],'export_validation':checks}
        print(name,classes,'max error',max(v['max_probability_error'] for v in checks),flush=True)
    (dest/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Export complete',dest,flush=True)

if __name__=='__main__':main()
