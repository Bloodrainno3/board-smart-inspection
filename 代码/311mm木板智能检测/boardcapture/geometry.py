"""Geometry in original image coordinates; line-scan axes are calibrated independently."""
from __future__ import annotations
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
import cv2
import numpy as np


def read_scaled(path, width=1024):
    # Decode unchanged orientation, matching U Net1.py (sensor coordinates).
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if image is None:
        raise ValueError("图像读取失败：" + str(path))
    h, w = image.shape[:2]
    if h * w > 400_000_000:
        raise ValueError("图像超过4亿像素，请分板处理")
    small = cv2.resize(image, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
    return small, {"width": w, "height": h, "scale_x": w / width, "scale_y": h / small.shape[0]}


def diagonal_intersection(corners):
    p = np.asarray(corners, dtype=np.float64)
    if p.shape != (4, 2) or not np.isfinite(p).all():
        raise ValueError("四边形坐标必须为4个有限二维点")
    a, b = p[2] - p[0], p[3] - p[1]
    mat = np.column_stack((a, -b))
    if abs(np.linalg.det(mat)) < 1e-9:
        raise ValueError("四边形退化，对角线没有唯一交点")
    t, u = np.linalg.solve(mat, p[1] - p[0])
    if not (0 <= t <= 1 and 0 <= u <= 1):
        raise ValueError("对角线交点不在四边形内部")
    return (p[0] + t * a).tolist()


def order_quad(points):
    p = np.asarray(points, np.float32).reshape(4, 2)
    # Use left/right pairs: a long thin sloping seam may have both lowest y
    # vertices on the left, so sorting by y would cross the polygon.
    p = p[np.argsort(p[:, 0])]
    left = p[:2][np.argsort(p[:2, 1])]
    right = p[2:][np.argsort(p[2:, 1])]
    return np.array([left[0], right[0], right[1], left[1]], np.float64)


def robust_line(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    keep = np.ones(len(x), bool)
    for _ in range(5):
        if keep.sum() < 10:
            raise ValueError("边线有效点不足")
        a, b = np.polyfit(x[keep], y[keep], 1)
        residual = y - (a * x + b)
        center = np.median(residual[keep])
        mad = np.median(np.abs(residual[keep] - center))
        keep = np.abs(residual-center) <= max(2.0, 4 * 1.4826 * mad)
    if keep.sum() < 10:
        raise ValueError("边线有效点不足")
    a,b=np.polyfit(x[keep],y[keep],1)
    residual=y-(a*x+b)
    return float(a), float(b), float(np.sqrt(np.mean(residual[keep] ** 2))), float(keep.mean())


def detect_board_top(bgr, meta):
    b, g, r = cv2.split(bgr.astype(np.int16))
    mask = ((r > 42) & (r > g * 1.06) & (r > b * 1.22)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 9), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    candidates = [i for i in range(1, n) if stats[i, cv2.CC_STAT_HEIGHT] > bgr.shape[0] * .25
                  and stats[i, cv2.CC_STAT_WIDTH] > bgr.shape[1] * .12]
    if not candidates:
        raise ValueError("未找到完整板材轮廓；请检查板头是否在图内、光照及背景")
    index = max(candidates, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    ax,ay,aw,ah,_=stats[index]
    # A dark full-width joint can disconnect the warm foreground into pieces.
    # Associate aligned pieces before fitting the *first* board end.
    selected={index};lo,hi=int(ay),int(ay+ah)
    changed=True
    while changed:
        changed=False
        for i in range(1,n):
            if i in selected:continue
            ix,iy,iw,ih,_=stats[i]
            overlap=max(0,min(ax+aw,ix+iw)-max(ax,ix))
            gap=max(0,lo-(iy+ih),iy-hi)
            if (.65*aw<=iw<=1.35*aw and ih>=max(15,bgr.shape[0]*.005)
                    and overlap>=.8*min(aw,iw) and gap<=max(30,.6*aw)):
                selected.add(i);lo=min(lo,int(iy));hi=max(hi,int(iy+ih));changed=True
    foreground=np.isin(labels,list(selected))
    yy,xx=np.where(foreground)
    x,y=int(xx.min()),int(yy.min());w,h=int(xx.max()-x+1),int(yy.max()-y+1)
    # The measuring footprint includes dark holes/gaps inside the board edges.
    # Warm colour is evidence for the outer edges, never a pixel veto on a seam.
    rows=np.where(foreground.any(axis=1))[0]
    left=foreground[rows].argmax(axis=1)
    right=bgr.shape[1]-1-foreground[rows,::-1].argmax(axis=1)
    all_rows=np.arange(rows[0],rows[-1]+1)
    ll=np.rint(np.interp(all_rows,rows,left)).astype(int)
    rr=np.rint(np.interp(all_rows,rows,right)).astype(int)
    board=np.zeros(foreground.shape,np.uint8)
    columns=np.arange(bgr.shape[1])[None,:]
    board[all_rows]=((columns>=ll[:,None])&(columns<=rr[:,None])).astype(np.uint8)
    xs = np.arange(x + max(3, int(w * .1)), x + int(w * .9))
    ys = np.argmax(foreground[:, xs], axis=0)
    # On a leaning board, some columns first meet a SIDE far below the head.
    # Restrict the top fit to the leading band before robust regression.
    leading=ys<=np.percentile(ys,20)+max(12,.25*w)
    a, intercept, rms, fraction = robust_line(xs[leading], ys[leading])
    fraction*=float(leading.mean())
    sx, sy = meta["scale_x"], meta["scale_y"]
    valid = bool(y > 2 and fraction >= .6 and rms <= 8 and w >= bgr.shape[1] * .15)
    top = {"valid": valid, "method": "leading_foreground_robust_top_line", "aligned_component_count":len(selected),
           "leading_edge_sample_fraction":float(leading.mean()),
           "slope": a * sy / sx, "intercept_px": intercept * sy,
           "endpoints_px": [[float(x*sx), float((a*x+intercept)*sy)],
                            [float((x+w-1)*sx), float((a*(x+w-1)+intercept)*sy)]],
           "fit_rms_px": rms*sy, "inlier_fraction": fraction,
           "board_bbox_px": [float(x*sx), float(y*sy), float(w*sx), float(h*sy)],
           "reason": "" if valid else "板头触及图像边界或边线拟合不稳定"}
    return top, board


def top_y(top, x):
    return top["slope"] * x + top["intercept_px"]


def calibrate(path, square_mm=0.0, paper_width_mm=210.0, paper_height_mm=297.0):
    image, meta = read_scaled(path, 1400)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    white = ((gray > 100) & ((image.max(2).astype(int)-image.min(2)) < 70)).astype(np.uint8)*255
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    paper = None
    for contour in contours:
        approx = cv2.approxPolyDP(contour, .015*cv2.arcLength(contour, True), True)
        if len(approx) == 4 and cv2.contourArea(approx) > image.shape[1]**2*.1:
            paper = order_quad(approx)
            break
    if paper is None:
        raise ValueError("未检测到完整A4纸外边界，请使用纸张四边都在画面内的标定照片")
    if paper[:,0].min() <= 1 or paper[:,1].min() <= 1 or paper[:,0].max() >= image.shape[1]-2 or paper[:,1].max() >= image.shape[0]-2:
        raise ValueError("标定纸触及图像边界，无法确定实际长度")
    # Search only the paper ROI, not the entire extremely long line-scan frame.
    x0,y0 = np.floor(paper.min(0)).astype(int)
    x1,y1 = np.ceil(paper.max(0)).astype(int)
    roi = gray[y0:y1+1,x0:x1+1]
    ok, corners = cv2.findChessboardCorners(roi, (6,6), flags=cv2.CALIB_CB_ADAPTIVE_THRESH)
    if ok:
        corners = cv2.cornerSubPix(roi, corners, (7,7), (-1,-1), (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 40, .001))
    else:
        ok, corners = cv2.findChessboardCornersSB(roi, (6,6), flags=cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE)
    if not ok:
        raise ValueError("A4纸已找到，但没有识别到6×6内角点（7×7方格）")
    corners = corners.reshape(6,6,2).astype(float) + [x0,y0]
    # Ensure grid axis 1 is image x; OpenCV may return a 90 degree orientation.
    if abs(np.diff(corners,axis=1)[...,0]).mean() < abs(np.diff(corners,axis=1)[...,1]).mean():
        corners = corners.transpose(1,0,2)
    if corners[0,-1,0] < corners[0,0,0]: corners = corners[:,::-1]
    if corners[-1,0,1] < corners[0,0,1]: corners = corners[::-1]
    factors = np.array([meta["scale_x"],meta["scale_y"]])
    paper *= factors
    corners *= factors
    dx = np.abs(np.diff(corners,axis=1)[...,0])
    dy = np.abs(np.diff(corners,axis=0)[...,1])
    spacing_x, spacing_y = float(np.median(dx)), float(np.median(dy))
    cvx,cvy = float(dx.std()/dx.mean()), float(dy.std()/dy.mean())
    width_px = float(np.mean([paper[1,0]-paper[0,0],paper[2,0]-paper[3,0]]))
    height_px = float(np.mean([paper[3,1]-paper[0,1],paper[2,1]-paper[1,1]]))
    if min(width_px,height_px,spacing_x,spacing_y) <= 0:
        raise ValueError("标定边长无效")
    if max(cvx,cvy) > .08:
        raise ValueError("格距变化超过8%，请检查纸张平整度、走纸速度及编码器")
    if square_mm > 0:
        sx, sy = square_mm/spacing_x, square_mm/spacing_y
        method = "measured_checker_square"
    else:
        sx, sy = paper_width_mm/width_px, paper_height_mm/height_px
        method = "full_A4_outer_boundary"
    inferred = [spacing_x*sx,spacing_y*sy]
    mismatch = abs(inferred[0]-inferred[1])/np.mean(inferred)
    warnings = []
    if mismatch > .05: warnings.append("A4外框与方格横纵比例偏差超过5%，请核对打印比例或输入实测格长")
    profile = {"version":1,"id":uuid.uuid4().hex,"created_at":datetime.now(timezone.utc).isoformat(),
               "method":method,"source":str(Path(path).resolve()),"image_width":meta["width"],
               "mm_per_pixel_x":sx,"mm_per_pixel_y":sy,"square_mm_input":square_mm,
               "paper_mm":[paper_width_mm,paper_height_mm],"paper_corners_px":paper.tolist(),
               "checker_corners_px":corners.reshape(-1,2).tolist(),"checker_pattern":[6,6],
               "checker_spacing_px":[spacing_x,spacing_y],"checker_spacing_cv":[cvx,cvy],
               "inferred_square_mm":inferred,"square_axis_mismatch":mismatch,"warnings":warnings,
               "assumptions":"同相机宽度、DPI/编码器倍率、走纸速度与纸张平面；纵向独立线性比例，非整板精度证明"}
    return profile, image, meta


def validate_profile(profile, image_width):
    if not profile: return False, "未应用现场标定"
    if profile.get("image_width") != image_width: return False, "图像宽度与标定不一致"
    if any(not np.isfinite(profile.get(k,0)) or profile.get(k,0)<=0 for k in ("mm_per_pixel_x","mm_per_pixel_y")):
        return False,"标定比例无效"
    if profile.get("warnings"): return False,"标定一致性检查未通过"
    return True,""
