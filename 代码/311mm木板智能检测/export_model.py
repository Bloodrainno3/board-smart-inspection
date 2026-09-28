"""Development only. Export the supplied weights; strict load, numerical comparison."""
from pathlib import Path
import argparse,hashlib,json,time
import numpy as np
import torch
import segmentation_models_pytorch as smp
import onnxruntime as ort
from boardcapture.config import atomic_json
from boardcapture.vision import normalize
from boardcapture.geometry import read_scaled

def main():
    p=argparse.ArgumentParser();p.add_argument('--weights',required=True);p.add_argument('--image',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    torch.set_num_threads(2)
    weights=Path(a.weights);out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    ckpt=torch.load(weights,map_location='cpu',weights_only=True)
    assert ckpt['arch']=='unetpp' and ckpt['encoder']=='resnet34'
    assert ckpt['target_width']==1024 and ckpt['tile_height']==1024
    model=smp.UnetPlusPlus(encoder_name='resnet34',encoder_weights=None,in_channels=3,classes=1).eval()
    model.load_state_dict(ckpt['model'],strict=True)
    image,meta=read_scaled(a.image)
    # A real texture tile, including a seam if supplied at this position.
    x=normalize(image[min(2048,len(image)-1024):min(2048,len(image)-1024)+1024])
    tensor=torch.from_numpy(x)
    with torch.inference_mode():
        expected=model(tensor).numpy()
        torch.onnx.export(model,tensor,str(out),input_names=['image'],output_names=['logits'],opset_version=17,dynamo=False)
    opts=ort.SessionOptions();opts.intra_op_num_threads=2;opts.inter_op_num_threads=1
    session=ort.InferenceSession(str(out),sess_options=opts,providers=['CPUExecutionProvider'])
    actual=session.run(None,{'image':x})[0]
    p1=1/(1+np.exp(-np.clip(expected,-80,80)));p2=1/(1+np.exp(-np.clip(actual,-80,80)))
    diff=float(np.max(np.abs(p1-p2)));agreement=float(((p1>=.5)==(p2>=.5)).mean())
    assert diff<.0001 and agreement>.99999,(diff,agreement)
    manifest={'arch':ckpt['arch'],'encoder':ckpt['encoder'],'target_width':1024,'tile_height':1024,'overlap':256,
        'threshold':ckpt['threshold'],'edge_margin_ratio':ckpt['edge_margin_ratio'],
        'normalization':{'order':'RGB','mean':[.485,.456,.406],'std':[.229,.224,.225],'divisor':255},
        'checkpoint_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),'onnx_sha256':hashlib.sha256(out.read_bytes()).hexdigest(),
        'torch_version':torch.__version__,'smp_version':smp.__version__,'onnxruntime_version':ort.__version__,
        'validation':{'image':a.image,'max_probability_error':diff,'binary_agreement':agreement,'strict_state_dict':True}}
    atomic_json(out.with_suffix('.json'),manifest)
    print(json.dumps(manifest,ensure_ascii=False))
if __name__=='__main__':main()
