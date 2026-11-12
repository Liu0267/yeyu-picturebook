"""Conservative scan checks and explicit geometry; original page assets stay immutable."""
import hashlib
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageOps


def inspect_page(path):
    with Image.open(path) as image:
        image=ImageOps.exif_transpose(image).convert('RGB');width,height=image.size
        image.thumbnail((1000,1000));rgb=np.asarray(image)
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
    contrast=float(gray.std());hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
    bright=(gray>250)&(hsv[:,:,1]<20);dark=gray<18
    flags=[]
    if min(width,height)<600:flags.append('small_image')
    if sharpness<35:flags.append('possible_blur_or_flat_artwork')
    if contrast<8:flags.append('low_contrast_or_blank_page')
    # White paper and dark artwork are frequent in picture books, so these only flag candidates.
    components=cv2.connectedComponentsWithStats(bright.astype('uint8'),8)[2][1:]
    glare=max((int(c[4]) for c in components),default=0)/gray.size
    if .015<glare<.55 and sharpness<250:flags.append('possible_glare_or_white_artwork')
    edge_dark=float(np.concatenate([dark[:max(1,len(dark)//10)].ravel(),dark[-max(1,len(dark)//10):].ravel()]).mean())
    if edge_dark>.2:flags.append('possible_occlusion_or_dark_border')
    edges=cv2.Canny(gray,60,160)
    contours,_=cv2.findContours(edges,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    suggested=None
    for c in sorted(contours,key=cv2.contourArea,reverse=True)[:10]:
        poly=cv2.approxPolyDP(c,.025*cv2.arcLength(c,True),True)
        if len(poly)==4 and cv2.isContourConvex(poly) and .3<cv2.contourArea(poly)/gray.size<.98:
            points=poly[:,0,:].astype(float);center=points.mean(axis=0)
            points=points[np.argsort(np.arctan2(points[:,1]-center[1],points[:,0]-center[0]))]
            points=np.roll(points,-np.argmin(points.sum(axis=1)),axis=0)
            suggested=(points/np.array([gray.shape[1]-1,gray.shape[0]-1])).tolist();break
    if suggested:flags.append('page_boundary_candidate_requires_review')
    return dict(source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),width=width,height=height,
                metrics=dict(laplacian_variance=round(sharpness,3),gray_std=round(contrast,3),bright_connected_fraction=round(glare,5),dark_edge_fraction=round(edge_dark,5)),
                quality_flags=flags,status='needs_review' if flags else 'no_heuristic_flag',suggested_corners=suggested,
                limitations=['Indicators are heuristic. White/dark artwork can trigger flags.',
                             'No automatic proof of missing pages, semantic occlusion or OCR accuracy. No automatic crop.'])


def rectify(source,target,rotation=0,corners=None):
    with Image.open(source) as image:rgb=np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
    h,w=rgb.shape[:2];matrix=np.eye(3,dtype=np.float64)
    if rotation==90:rgb=cv2.rotate(rgb,cv2.ROTATE_90_CLOCKWISE);matrix=np.array([[0,-1,h-1],[1,0,0],[0,0,1]],float)
    elif rotation==180:rgb=cv2.rotate(rgb,cv2.ROTATE_180);matrix=np.array([[-1,0,w-1],[0,-1,h-1],[0,0,1]],float)
    elif rotation==270:rgb=cv2.rotate(rgb,cv2.ROTATE_90_COUNTERCLOCKWISE);matrix=np.array([[0,1,0],[-1,0,w-1],[0,0,1]],float)
    elif rotation!=0:raise ValueError('rotation must be 0/90/180/270')
    if corners is not None:
        points=np.asarray(corners,dtype=np.float32)
        if points.shape!=(4,2) or not np.isfinite(points).all() or (points<0).any() or (points>1).any():raise ValueError('four normalized corners required')
        # Ordered TL,TR,BR,BL in the rotated image; reject crossing/degenerate geometry.
        if not cv2.isContourConvex(points.reshape(-1,1,2)) or cv2.contourArea(points)<.01:raise ValueError('corners must form a convex ordered quadrilateral')
        h,w=rgb.shape[:2];points*=np.array([w-1,h-1],np.float32)
        width=round(max(np.linalg.norm(points[1]-points[0]),np.linalg.norm(points[2]-points[3])))+1
        height=round(max(np.linalg.norm(points[3]-points[0]),np.linalg.norm(points[2]-points[1])))+1
        transform=cv2.getPerspectiveTransform(points,np.array([[0,0],[width-1,0],[width-1,height-1],[0,height-1]],np.float32))
        rgb=cv2.warpPerspective(rgb,transform,(width,height));matrix=transform@matrix
    Image.fromarray(rgb).save(target)
    return dict(rotation_clockwise=rotation,corners_after_rotation=corners,source_to_result_pixel_matrix=matrix.tolist(),
                result_to_source_pixel_matrix=np.linalg.inv(matrix).tolist(),original_unchanged=True)
