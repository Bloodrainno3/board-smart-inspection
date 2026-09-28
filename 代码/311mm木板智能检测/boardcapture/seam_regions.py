"""Recover complete predicted components from row candidates without inventing masks."""
import cv2
import numpy as np


def complete_regions(binary,groups,max_seams):
    _,labels,stats,_=cv2.connectedComponentsWithStats(binary.astype(np.uint8),connectivity=8)
    selections=[]
    for group in groups:
        ids=set(np.unique(labels[group['y0']:group['y1']]).tolist())-{0}
        if not ids:continue
        score=group['coverage_score']
        merged=[]
        for existing in selections:
            if ids & existing['ids']:
                ids|=existing['ids'];score=max(score,existing['coverage_score'])
            else:merged.append(existing)
        merged.append({'ids':ids,'coverage_score':score});selections=merged
    selections.sort(key=lambda g:g['coverage_score'],reverse=True)
    truncated=len(selections)>max_seams
    results=[]
    for item in selections[:max_seams]:
        ids=list(item.pop('ids'));component_stats=stats[ids]
        y0=int(component_stats[:,cv2.CC_STAT_TOP].min())
        y1=int((component_stats[:,cv2.CC_STAT_TOP]+component_stats[:,cv2.CC_STAT_HEIGHT]).max())
        region=np.isin(labels[y0:y1],ids)
        results.append(dict(item,y0=y0,y1=y1,region=region,component_count=len(ids)))
    return sorted(results,key=lambda g:g['y0']),truncated
