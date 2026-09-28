"""Orientation-aware model regions and image-checked pairing of wide seam edges.

The network mask is preserved separately. Any filled band is explicitly a
geometric refinement from two predicted boundaries, requiring review.
"""
import cv2
import numpy as np
from .geometry import robust_line


def _describe(xs,ys,ids):
    order=np.argsort(xs,kind='stable');sorted_y=ys[order]
    unique,starts,counts=np.unique(xs[order],return_index=True,return_counts=True)
    medians=np.array([np.median(sorted_y[start:start+count]) for start,count in zip(starts,counts)])
    stable=True
    if len(unique)>=10:
        try:slope,intercept,_,_=robust_line(unique,medians)
        except ValueError:
            slope,intercept=map(float,np.polyfit(unique,medians,1));stable=False
    else:
        slope=0.;intercept=float(np.median(ys))
    residual=ys-(slope*xs+intercept)
    thickness=float(np.percentile(residual,95)-np.percentile(residual,5)+1)
    width=int(xs.max()-xs.min()+1)
    return dict(xs=xs,ys=ys,ids=ids,slope=slope,intercept=intercept,
                thickness=thickness,x0=int(xs.min()),x1=int(xs.max()),width=width,
                support=len(unique),cy=float(slope*np.mean([xs.min(),xs.max()])+intercept),
                boundary=bool(stable and abs(slope)<=.35 and thickness<=max(12,.035*width)))


def _appearance(image,upper,lower,x0,x1):
    """Check that the enclosed strip differs consistently from BOTH neighbours."""
    xs=np.linspace(x0+.1*(x1-x0),x1-.1*(x1-x0),64)
    a=upper['slope']*xs+upper['intercept'];b=lower['slope']*xs+lower['intercept']
    gap=b-a
    lo=max(0,int(np.floor(a.min()))-45);hi=min(len(image),int(np.ceil(b.max()))+46)
    lab=cv2.cvtColor(image[lo:hi],cv2.COLOR_BGR2LAB).astype(np.float32)
    def sample(ys):
        yy=(np.rint(ys).astype(int)-lo).clip(0,len(lab)-1)
        xx=np.broadcast_to(np.rint(xs).astype(int),yy.shape)
        return np.median(lab[yy,xx],axis=0)
    inside=sample(a[None,:]+gap[None,:]*np.array([.2,.35,.5,.65,.8])[:,None])
    offset=np.maximum(6,np.minimum(30,gap*.3))
    before=sample(a[None,:]-offset[None,:]*np.array([.6,1,1.4])[:,None])
    after=sample(b[None,:]+offset[None,:]*np.array([.6,1,1.4])[:,None])
    # Average across local wood-grain columns; an individual dark grain should
    # not dominate the comparison between the band and its neighbours.
    inside=inside.reshape(8,8,3).mean(1)
    before=before.reshape(8,8,3).mean(1);after=after.reshape(8,8,3).mean(1)
    # L has a larger encoded range than a/b; retain luminance as well as chroma.
    weights=np.array([.5,1,1],np.float32)
    du=(inside-before)*weights;dl=(inside-after)*weights
    nu=np.linalg.norm(du,axis=1);nl=np.linalg.norm(dl,axis=1)
    agreement=(du*dl).sum(1)/(nu*nl+1e-6)
    fraction=float(((nu>=5)&(nl>=5)&(agreement>.3)).mean())
    return {'consistent_band_fraction':fraction,'upper_contrast':float(np.median(nu)),
            'lower_contrast':float(np.median(nl)),'accepted':bool(fraction>=.65)}


def detect_regions(binary,image,min_horizontal_coverage=.08,max_seams=2):
    """Select by horizontal projected support, then pair nearby supported edges."""
    _,labels,stats,_=cv2.connectedComponentsWithStats(binary.astype(np.uint8),connectivity=8)
    parts=[]
    for label in range(1,len(stats)):
        x,y,w,h,area=map(int,stats[label])
        if area<6 or w<5:continue
        yy,xx=np.where(labels[y:y+h,x:x+w]==label)
        parts.append(_describe(xx+x,yy+y,{label}))
    parts.sort(key=lambda p:p['support'],reverse=True)
    merged=[]
    while parts:
        current=parts.pop(0)
        if current['boundary']:
            changed=True
            while changed:
                changed=False
                for other in list(parts):
                    if not other['boundary']:continue
                    gap=max(0,other['x0']-current['x1'],current['x0']-other['x1'])
                    if gap>image.shape[1]*.25:continue
                    short=other if other['width']<=current['width'] else current
                    # Test a short fragment on its observed span. Extrapolating
                    # its noisy slope across the whole board loses valid pieces.
                    xx=np.array([short['x0'],short['x1']])
                    disagreement=np.abs((current['slope']-other['slope'])*xx+current['intercept']-other['intercept'])
                    if disagreement.max()>max(4,current['thickness']+other['thickness']):continue
                    proposal=_describe(np.r_[current['xs'],other['xs']],np.r_[current['ys'],other['ys']],current['ids']|other['ids'])
                    if not proposal['boundary']:continue
                    current=proposal;parts=[p for p in parts if p is not other];changed=True
        merged.append(current)
    minimum=max(50,min_horizontal_coverage*image.shape[1])
    candidates=sorted([p for p in merged if p['support']>=minimum],key=lambda p:p['cy'])
    paired=[];checks=[];i=0
    while i<len(candidates):
        upper=candidates[i]
        if i+1<len(candidates) and upper['boundary'] and candidates[i+1]['boundary']:
            lower=candidates[i+1]
            overlap=max(0,min(upper['x1'],lower['x1'])-max(upper['x0'],lower['x0'])+1)
            x0=min(upper['x0'],lower['x0']);x1=max(upper['x1'],lower['x1'])
            xx=np.linspace(x0,x1,32)
            top=upper['slope']*xx+upper['intercept'];bottom=lower['slope']*xx+lower['intercept']
            gap=bottom-top;median=float(np.median(gap))
            geometry_ok=(overlap>=.65*max(upper['width'],lower['width'])
                         and min(upper['support'],lower['support'])>=.4*(x1-x0+1)
                         and gap.min()>=max(8,2*max(upper['thickness'],lower['thickness']))
                         and gap.max()<=.6*(x1-x0+1) and gap.min()>=.6*median)
            if geometry_ok:
                evidence=_appearance(image,upper,lower,x0,x1)
                checks.append(dict(upper_y=upper['cy'],lower_y=lower['cy'],**evidence))
                if evidence['accepted']:
                    quad=np.array([[x0,top[0]],[x1,top[-1]],[x1,bottom[-1]],[x0,bottom[0]]],np.float32)
                    paired.append(dict(parts=[upper,lower],quad=quad,evidence=evidence));i+=2;continue
        paired.append(dict(parts=[upper]));i+=1
    results=[]
    for item in paired:
        part_list=item['parts'];ids=set().union(*(p['ids'] for p in part_list))
        yy=np.concatenate([p['ys'] for p in part_list])
        y0,y1=int(yy.min()),int(yy.max()+1)
        quad=item.get('quad')
        if quad is not None:
            y0=max(0,min(y0,int(np.floor(quad[:,1].min()))))
            y1=min(len(binary),max(y1,int(np.ceil(quad[:,1].max()))+1))
        model_region=np.isin(labels[y0:y1],list(ids))
        region=model_region.copy()
        if quad is not None:
            polygon=np.rint(quad-[0,y0]).astype(np.int32)
            filled=np.zeros(region.shape,np.uint8);cv2.fillConvexPoly(filled,polygon,1)
            region=filled>0
        results.append(dict(y0=y0,y1=y1,region=region,model_region=model_region,
                            component_count=len(ids),coverage_score=max(p['support'] for p in part_list)/image.shape[1],
                            paired_boundary=quad is not None,quad=quad,evidence=item.get('evidence')))
    results.sort(key=lambda g:g['coverage_score'],reverse=True)
    truncated=len(results)>max_seams
    info={'component_count':len(stats)-1,'projected_candidate_count':len(candidates),
          'regions_before_limit':len(results),'boundary_pair_checks':checks,
          'candidates':[{k:p[k] for k in ('x0','x1','cy','slope','thickness','support','boundary')} for p in candidates]}
    return sorted(results[:max_seams],key=lambda g:g['y0']),truncated,info
