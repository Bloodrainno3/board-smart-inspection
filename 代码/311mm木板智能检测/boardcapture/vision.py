"""UNet++ inference compatible with the user's U Net1.py; ONNX deployment."""
from __future__ import annotations
import csv
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import cv2
import numpy as np
from .config import atomic_json
from .geometry import read_scaled, detect_board_top, diagonal_intersection, order_quad, top_y, validate_profile
from .boundary_regions import detect_regions


def tile_starts(height, tile=1024, overlap=256):
    if height <= tile: return [0]
    starts = list(range(0, height-tile+1, tile-overlap))
    if starts[-1] != height-tile: starts.append(height-tile)
    return starts


def normalize(bgr):
    rgb = bgr[...,::-1].astype(np.float32)/255.0
    rgb = (rgb-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
    return np.ascontiguousarray(rgb.transpose(2,0,1)[None])


def select_seams(prob, threshold=.5, edge_margin_ratio=.03, max_seams=2, min_row_coverage=.08):
    # Same row selection, sigma=2 and close=7 as the supplied prediction script.
    binary = prob >= threshold
    edge = round(len(prob)*edge_margin_ratio)
    if edge:
        binary[:edge] = False
        binary[-edge:] = False
    coverage = binary.mean(axis=1).astype(np.float32)
    score = cv2.GaussianBlur(coverage[:,None], (1,17), 2, borderType=cv2.BORDER_REFLECT)[:,0]
    candidate = (score >= min_row_coverage).astype(np.uint8)[:,None]
    candidate = cv2.morphologyEx(candidate,cv2.MORPH_CLOSE,np.ones((7,1),np.uint8))[:,0]
    changes = np.diff(np.r_[0,candidate,0].astype(int))
    groups = [{"y0":int(a),"y1":int(b),"coverage_score":float(score[a:b].max())}
              for a,b in zip(np.where(changes==1)[0],np.where(changes==-1)[0])]
    truncated = len(groups) > max_seams
    groups = sorted(sorted(groups,key=lambda g:g["coverage_score"],reverse=True)[:max_seams],key=lambda g:g["y0"])
    return binary, groups, truncated


class Predictor:
    def __init__(self, model_path, threads=2):
        import onnxruntime as ort
        self.path = Path(model_path)
        manifest = json.loads(self.path.with_suffix('.json').read_text(encoding='utf-8'))
        self.manifest = manifest
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if digest != manifest['onnx_sha256']:
            raise ValueError("模型文件校验失败，请重新复制完整模型文件")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.add_session_config_entry('session.intra_op.allow_spinning','0')
        self.session = ort.InferenceSession(str(self.path),sess_options=opts,providers=['CPUExecutionProvider'])

    def probability(self, image, progress=None):
        h,w = image.shape[:2]
        sums = np.zeros((h,w),np.float32)
        counts = np.zeros((h,1),np.float32)
        starts = tile_starts(h)
        for i,y in enumerate(starts):
            tile=image[y:y+1024]
            valid=len(tile)
            if valid<1024: tile=cv2.copyMakeBorder(tile,0,1024-valid,0,0,cv2.BORDER_CONSTANT,value=(0,0,0))
            logits=self.session.run(None,{'image':normalize(tile)})[0][0,0,:valid]
            prob=1/(1+np.exp(-np.clip(logits,-80,80)))
            sums[y:y+valid]+=prob
            counts[y:y+valid]+=1
            if progress:progress(i+1,len(starts))
        return sums/np.maximum(counts,1)

    def analyze(self, path, out_dir, settings, profile=None, progress=None):
        started=time.monotonic()
        out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
        image,meta=read_scaled(path)
        prob=self.probability(image,progress)
        top_error=''
        try: top,board=detect_board_top(image,meta)
        except ValueError as e:
            top={'valid':False,'reason':str(e)};board=np.ones(prob.shape,np.uint8);top_error=str(e)
        # Colour/top estimation is independent evidence, not a veto on the model.
        # A wide dark seam may split the colour footprint into separate boards.
        threshold=settings.get('threshold',.5)
        raw_binary=prob>=threshold
        binary,candidates,_=select_seams(prob,threshold,.03,
                                            len(prob),settings.get('min_row_coverage',.08))
        groups,truncated,region_diagnostics=detect_regions(binary,image,settings.get('min_row_coverage',.08),settings.get('max_seams',2))
        calibrated,calibration_reason=validate_profile(profile,meta['width'])
        factors=np.array([meta['scale_x'],meta['scale_y']])
        seams=[];mask=np.zeros(prob.shape,np.uint8);geometry_rejections=[]
        for group in groups:
            y0,y1=group['y0'],group['y1']
            region=group['region']
            ys,xs=np.where(region)
            if len(xs)<10 or np.ptp(xs)<50:
                geometry_rejections.append({'y0':y0,'y1':y1,'reason':'区域像素不足10或横向跨度不足50（1024宽缩略图）'})
                continue
            # Semantic masks have no inherent four vertices. Enclose the selected
            # region by a minimum-area rectangle and intersect its diagonals.
            pts=np.column_stack([xs,ys+y0]).astype(np.float32)
            rectangle=cv2.minAreaRect(pts)
            paired=group['paired_boundary']
            quad=(group['quad'].astype(np.float64) if paired else order_quad(cv2.boxPoints(rectangle)))*factors
            try: center=diagonal_intersection(quad)
            except ValueError as e:
                geometry_rejections.append({'y0':y0,'y1':y1,'reason':str(e)})
                continue
            yt=top_y(top,center[0]) if top['valid'] else None
            dy=center[1]-yt if yt is not None else None
            footprint_fraction=float((board[y0:y1][region]>0).mean()) if not top_error else None
            footprint_conflict=footprint_fraction is not None and footprint_fraction<.5
            valid=bool(dy is not None and dy>0 and not footprint_conflict)
            seam={"index":len(seams)+1,"corners_order":"TL,TR,BR,BL",
                  "quadrilateral_method":"fitted_upper_lower_boundary_quadrilateral" if paired else "minimum_area_rectangle_of_complete_model_components",
                  "region_method":"image_checked_model_boundary_pair" if paired else "complete_model_components_without_colour_clipping",
                  "boundary_pair_completed":paired,"boundary_pair_evidence":group['evidence'],
                  "model_component_count":group['component_count'],
                  "board_footprint_fraction":footprint_fraction,"board_footprint_conflict":footprint_conflict,
                  "region_review_required":bool(paired or np.ptp(ys)>.5*np.ptp(xs) or footprint_conflict),
                  "corners_px":quad.tolist(),"center_px":center,"top_at_center_x_px":yt,
                  "distance_from_board_top_px":dy if valid else None,"distance_from_board_top_mm":dy*profile['mm_per_pixel_y'] if valid and calibrated else None,
                  "coordinate_valid":valid,"calibrated":calibrated,"mean_probability":float(prob[y0:y1][group['model_region']].mean()),
                  "coverage_score":group['coverage_score'],"coverage_method":"horizontal_projected_columns",
                  "segmented_area_px":float(group['model_region'].sum()*np.prod(factors)),
                  "measurement_area_px":float(len(xs)*np.prod(factors))}
            seams.append(seam)
            mask[y0:y1][region]=255
        raw_count=int(raw_binary.sum());eligible_count=int(binary.sum())
        if not raw_count:
            detection_stage='no_model_pixels';explanation='模型在当前分割阈值下没有预测像素；需核对原始板图、拍摄条件与训练样本'
        elif not eligible_count:
            detection_stage='edge_margin_only';explanation='模型预测仅在图像首尾3%范围，被边缘排除规则滤掉'
        elif not region_diagnostics['projected_candidate_count']:
            detection_stage='horizontal_support_filter';explanation='模型有预测像素，但横向覆盖不足，未形成横缝候选'
        elif not seams:
            detection_stage='geometry_filter';explanation='有候选缝区，但区域过小或四边形退化，无法计算中心'
        else:
            detection_stage='seams_retained';explanation=f"保留 {len(seams)} 个缝区，其中 {sum(s['boundary_pair_completed'] for s in seams)} 个由模型上下边界与原图色带配对补全"
        diagnostics={'stage':detection_stage,'explanation':explanation,'scaled_width':prob.shape[1],
                     'scaled_height':prob.shape[0],'model_threshold_pixels':raw_count,
                     'after_edge_margin_pixels':eligible_count,'edge_margin_ratio':.03,
                     'row_candidate_count':len(candidates),'legacy_row_filter_applied':False,'region_selection':region_diagnostics,
                     'selected_region_count':len(groups),
                     'seam_count':len(seams),'candidate_limit_reached':truncated,
                     'geometry_rejections':geometry_rejections,'colour_filter_applied':False,
                     'model_pixels_outside_colour_footprint':int(np.count_nonzero(raw_binary & (board==0))) if not top_error else None}
        warnings=[]
        if not top['valid']:warnings.append(top.get('reason',top_error))
        if not calibrated:warnings.append(calibration_reason)
        if truncated:warnings.append('完整候选缝区多于设定上限，仅保留评分最高的候选；请复核')
        if not seams:warnings.append(explanation)
        if any(not s['coordinate_valid'] for s in seams):warnings.append('板顶或板材范围与候选缝区不一致，保留中心坐标，暂停输出该缝的板顶距离')
        if any(s['boundary_pair_completed'] for s in seams):warnings.append('宽缝区域由模型上下边界及原图色带配对补全；中间填充部分不是模型直接分割结果，请复核四角与中心')
        if any(s['region_review_required'] and not s['boundary_pair_completed'] for s in seams):warnings.append('候选区域跨度较大或与板材轮廓冲突；请对照模型原始掩膜和原图复核')
        status='measured' if seams and all(s['coordinate_valid'] and not s['region_review_required'] for s in seams) and calibrated and not truncated else 'review_required'
        result={"schema_version":2,"postprocess_version":"2026.09.18_oriented_boundary_pairs", "board_id":Path(path).stem,"source":str(Path(path).resolve()),
                "material":settings.get('material','混合板材'),"created_at":datetime.now(timezone.utc).isoformat(),
                "image_width":meta['width'],"image_height":meta['height'],"coordinate_system":"原始图像左上角(0,0)，x向右，y向下；板顶同x处为纵向零点",
                "model_architecture":"UNet++/ResNet34","model_checkpoint_sha256":self.manifest['checkpoint_sha256'],
                "model_onnx_sha256":self.manifest['onnx_sha256'],"inference_settings":settings,
                "calibration":profile,"calibration_id":profile.get('id') if profile else None,
                "board_top":top,"seams":seams,"seam_count":len(seams),"status":status,"warnings":warnings,"detection_diagnostics":diagnostics,
                "processing_seconds":time.monotonic()-started,"cutter_command_sent":False}
        overlay=image.copy()
        overlay[mask>0]=(overlay[mask>0]*.35+np.array([0,0,255])*.65).astype(np.uint8)
        if top['valid']:
            a,b=np.rint(np.asarray(top['endpoints_px'])/factors).astype(int)
            cv2.line(overlay,tuple(a),tuple(b),(255,255,0),3)
        for seam in seams:
            quad=np.rint(np.array(seam['corners_px'])/factors).astype(int)
            c=tuple(np.rint(np.array(seam['center_px'])/factors).astype(int))
            cv2.polylines(overlay,[quad],True,(0,255,255),2)
            cv2.line(overlay,tuple(quad[0]),tuple(quad[2]),(0,255,0),2)
            cv2.line(overlay,tuple(quad[1]),tuple(quad[3]),(0,255,0),2)
            cv2.circle(overlay,c,7,(255,0,255),-1)
            if seam['coordinate_valid']:
                ty=round(seam['top_at_center_x_px']/factors[1]);cv2.line(overlay,(c[0],ty),c,(255,160,0),2)
            unit=seam['distance_from_board_top_mm']
            label=f"S{seam['index']} y={seam['center_px'][1]:.1f}px / "+(f'{unit:.2f}mm' if unit is not None else 'uncalibrated')
            cv2.putText(overlay,label,(10,max(30,c[1]-25)),cv2.FONT_HERSHEY_SIMPLEX,.65,(0,255,255),2)
            detail_lo=max(0,min(c[1]-150,int(quad[:,1].min())-60))
            detail_hi=min(len(overlay),max(c[1]+151,int(quad[:,1].max())+61))
            cv2.imencode('.jpg',overlay[detail_lo:detail_hi],[cv2.IMWRITE_JPEG_QUALITY,95])[1].tofile(str(out/f"seam_{seam['index']}_detail.jpg"))
        cv2.imencode('.jpg',overlay,[cv2.IMWRITE_JPEG_QUALITY,92])[1].tofile(str(out/'overlay.jpg'))
        cv2.imencode('.png',mask)[1].tofile(str(out/'mask_1024.png'))
        cv2.imencode('.png',raw_binary.astype(np.uint8)*255)[1].tofile(str(out/'model_mask_1024.png'))
        cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,95])[1].tofile(str(out/'source_1024.jpg'))
        result['model_mask']=str(out/'model_mask_1024.png')
        result['measurement_mask']=str(out/'mask_1024.png')
        result['source_preview']=str(out/'source_1024.jpg')
        result['overlay']=str(out/'overlay.jpg')
        result['result_file']=str(out/'result.json')
        fields=['board_id','material','index','center_x_px','center_y_px','top_y_px','distance_px','distance_mm','status','calibration_id','corners_px']
        with (out/'parameters.csv.part').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            for s in seams:
                writer.writerow(dict(board_id=result['board_id'],material=result['material'],index=s['index'],center_x_px=s['center_px'][0],
                    center_y_px=s['center_px'][1],top_y_px=s['top_at_center_x_px'],distance_px=s['distance_from_board_top_px'],
                    distance_mm=s['distance_from_board_top_mm'],status=status,calibration_id=result['calibration_id'],corners_px=json.dumps(s['corners_px'])))
        (out/'parameters.csv.part').replace(out/'parameters.csv')
        # Completion marker is published after every image and CSV is finished.
        atomic_json(out/'result.json',result)
        return result
