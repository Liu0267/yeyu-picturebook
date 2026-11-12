import hashlib
import numpy as np
import pytest
from PIL import Image,ImageDraw,ImageFilter
from generation.quality import inspect_page,rectify


def test_blurred_input_is_flagged_and_original_is_preserved(tmp_path):
    im=Image.new('RGB',(800,1000),'white');d=ImageDraw.Draw(im)
    for y in range(100,900,30):d.text((70,y),'DOCUMENT LINE '+str(y),fill='black',font_size=22)
    sharp=tmp_path/'sharp.png';blur=tmp_path/'blur.png';im.save(sharp);im.filter(ImageFilter.GaussianBlur(12)).save(blur)
    assert inspect_page(blur)['metrics']['laplacian_variance']<inspect_page(sharp)['metrics']['laplacian_variance']/10
    assert 'possible_blur_or_flat_artwork' in inspect_page(blur)['quality_flags']
    before=hashlib.sha256(sharp.read_bytes()).hexdigest();out=tmp_path/'rotated.png';receipt=rectify(sharp,out,90)
    assert Image.open(out).size==(1000,800) and receipt['original_unchanged']
    assert hashlib.sha256(sharp.read_bytes()).hexdigest()==before


def test_perspective_transform_maps_corners_and_rejects_crossed_input(tmp_path):
    source=tmp_path/'source.png';Image.new('RGB',(800,600),'yellow').save(source)
    corners=[[.1,.1],[.9,.15],[.85,.9],[.15,.85]]
    out=tmp_path/'rectified.png';result=rectify(source,out,corners=corners)
    h=np.asarray(result['source_to_result_pixel_matrix']);p=np.array([.1*799,.1*599,1]);q=h@p
    assert np.allclose(q[:2]/q[2],[0,0],atol=.001)
    with pytest.raises(ValueError):rectify(source,out,corners=[[0,0],[1,1],[1,0],[0,1]])
    with pytest.raises(ValueError):rectify(source,out,corners=[[0,0],[0,0],[0,0],[0,0]])


def test_bright_artwork_is_not_declared_definite_glare(tmp_path):
    source=tmp_path/'page.png';Image.new('RGB',(800,1000),'white').save(source)
    result=inspect_page(source)
    assert result['status']=='needs_review' and result['limitations']
    assert 'glare_detected' not in result['quality_flags']
