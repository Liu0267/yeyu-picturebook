"""Actual color frames provide independent ground truth for low-FPS extraction."""
import subprocess
import wave
from types import SimpleNamespace
import pytest
from PIL import Image
from generation.media import extract_frames,render_narration,probe
from generation.store import Store


@pytest.fixture
def lowfps_media(tmp_path):
    store=Store(tmp_path/'data')
    inputs=store.root/'inputs';inputs.mkdir()
    for i in range(8):Image.new('RGB',(64,64),(i*30,50,200)).save(inputs/f'{i:03}.png')
    video=store.root/'lowfps.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-framerate','8','-i',str(inputs/'%03d.png'),
                    '-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True)
    clip=SimpleNamespace(clip_id='lowfps',asset_version=1,asset_id=store.asset(video,'video/mp4',True),is_mock=True)
    return store,clip,video


def test_low_fps_last_frame_uses_real_pts_and_matching_pixels(lowfps_media):
    store,clip,video=lowfps_media
    frames=extract_frames(store,clip)
    assert [f.t_ms for f in frames]==[0,500,875]
    for frame,red in zip(frames,[0,120,210]):
        with Image.open(store.asset_path(frame.image_asset_id)) as image:
            assert abs(image.getpixel((32,32))[0]-red)<8


def test_narration_tail_records_actual_frame_rounded_duration(lowfps_media):
    store,clip,video=lowfps_media
    audio=store.root/'silence.wav'
    with wave.open(str(audio),'wb') as f:
        f.setparams((1,2,24000,0,'NONE','not compressed'));f.writeframes(b'\x00\x00'*24000)
    output=store.root/'narrated';output.mkdir()
    target,vtt,timing=render_narration(video,audio,'合成测试。',output)
    assert timing['requested_tail_extension_ms']==450
    assert timing['tail_extension_ms']==500
    assert timing['actual_video_duration_ms']==probe(target)[0]==1500
