"""Optional row separation for reviewed black-on-light Chinese/pinyin text regions."""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image


def chinese_rows(source,region,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with Image.open(source) as image:
        image=image.convert('RGB');w,h=image.size
        left,top,right,bottom=[round(v*s) for v,s in zip(region,[w,h,w,h])]
        crop=image.crop((left,top,right,bottom));gray=np.asarray(crop.convert('L'))
    binary=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
    active=(binary>0).sum(axis=1)>max(3,gray.shape[1]*.012)
    bands=[];start=None
    for y,value in enumerate(list(active)+[False]):
        if value and start is None:start=y
        elif not value and start is not None:bands.append((start,y));start=None
    longest=max((b-a for a,b in bands),default=0)
    result=[]
    for a,b in bands:
        if b-a<max(8,longest*.60):continue
        a=max(0,a-3);b=min(gray.shape[0],b+3)
        path=output/f'row_{len(result)+1:02}.png';crop.crop((0,a,crop.width,b)).resize((crop.width*2,(b-a)*2),Image.Resampling.LANCZOS).save(path)
        result.append(dict(image=str(path),source_region=[left/w,(top+a)/h,right/w,(top+b)/h]))
    return result
