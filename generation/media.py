"""Real media metadata and frame extraction; no claims inferred from prompts."""
import json
import math
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw
from .models import FrameEvidence


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True, timeout=30)
    data = json.loads(result.stdout)
    data.get('format', {}).pop('filename', None)
    if not any(s['codec_type'] == 'video' for s in data['streams']):
        raise ValueError('media has no video stream')
    return max(1, round(float(data['format']['duration']) * 1000)), data


def make_mock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', (640, 360), '#eef3f0')
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((200, 90, 440, 270), radius=12, fill='#fff9e8', outline='#345e58', width=5)
    draw.line((320, 95, 320, 266), fill='#345e58', width=3)
    draw.ellipse((270, 130, 305, 165), fill='#d38847')
    draw.text((22, 20), 'MOCK - SYNTHETIC PROTOCOL TEST - NO AI VIDEO', fill='#9c362a', font_size=20)
    draw.text((190, 310), 'Book / Page / Clip / FrameEvidence', fill='#345e58', font_size=16)
    image.save(directory / 'mock.png')
    video = directory / 'mock.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-loop', '1', '-i', str(directory/'mock.png'),
                    '-t', '3', '-r', '24', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(video)],
                   check=True, timeout=60)
    return video


def extract_frames(store, clip):
    video = store.asset_path(clip.asset_id)
    duration, metadata = probe(video)
    timestamps=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
                                'frame=best_effort_timestamp_time','-of','json',str(video)],
                               capture_output=True,text=True,check=True,timeout=30)
    candidates=[(i,float(f['best_effort_timestamp_time'])) for i,f in enumerate(json.loads(timestamps.stdout)['frames'])
                if 'best_effort_timestamp_time' in f and float(f['best_effort_timestamp_time'])>=0]
    if not candidates:raise ValueError('media has no timestamped video frames')
    targets={0,duration//2,max(0,duration-100)}
    selected=sorted({min(candidates,key=lambda f:abs(f[1]-t/1000)) for t in targets})
    metadata['evidence_extraction']=dict(method='frame_index_from_ffprobe_pts',
                                        frames=[dict(frame_index=i,pts_seconds=t) for i,t in selected])
    store.put('media_probe', clip.clip_id, metadata)
    record = store.get('asset', clip.asset_id)
    out = store.root / 'frames' / clip.clip_id / str(clip.asset_version)
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, (frame_index,pts) in enumerate(selected,1):
        t=math.ceil(pts*1000-1e-6)
        target = out / f'{t:06}.png'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(video), '-vf', f'select=eq(n\\,{frame_index})',
                        '-frames:v', '1', str(target)], check=True, timeout=30)
        frame = FrameEvidence(frame_id=f'{clip.clip_id}_v{clip.asset_version}_f{i}', clip_id=clip.clip_id,
                               asset_version=clip.asset_version, t_ms=t,
                               image_asset_id=store.asset(target, 'image/png', clip.is_mock),
                               video_sha256=record['sha256'], is_mock=clip.is_mock)
        store.put('frame', frame.frame_id, frame)
        frames.append(frame)
    return frames


def render_narration(video,audio,text,output_dir):
    """Single-utterance subtitles use actual WAV duration, not estimated reading speed."""
    output=Path(output_dir)
    audio_probe=subprocess.run(['ffprobe','-v','error','-show_format','-of','json',str(audio)],capture_output=True,text=True,check=True,timeout=30)
    audio_ms=round(float(json.loads(audio_probe.stdout)['format']['duration'])*1000)
    video_ms,_=probe(video)
    duration=max(video_ms,audio_ms+450)
    def stamp(ms,separator=','):
        hours,remainder=divmod(ms,3600000);minutes,remainder=divmod(remainder,60000);seconds,millis=divmod(remainder,1000)
        return f'{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}'
    start,end=200,200+audio_ms
    (output/'subtitles.srt').write_text(f'1\n{stamp(start)} --> {stamp(end)}\n{text}\n',encoding='utf-8')
    vtt=output/'subtitles.vtt'
    vtt.write_text(f'WEBVTT\n\n{stamp(start,".")} --> {stamp(end,".")}\n{text}\n',encoding='utf-8')
    target=output/'narrated.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(video),'-i',str(audio),'-i',str(output/'subtitles.srt'),
                    '-map','0:v:0','-map','1:a:0','-map','2:0','-vf',f'tpad=stop_mode=clone:stop_duration={max(0,duration-video_ms)/1000:.3f}',
                    '-af','adelay=200:all=1,apad','-t',f'{duration/1000:.3f}','-c:v','libx264','-crf','18','-pix_fmt','yuv420p',
                    '-c:a','aac','-c:s','mov_text','-movflags','+faststart',str(target)],check=True,timeout=120)
    rendered_ms,_=probe(target)
    return target,vtt,dict(audio_duration_ms=audio_ms,subtitle_start_ms=start,subtitle_end_ms=end,
                           requested_tail_extension_ms=max(0,duration-video_ms),tail_extension_ms=max(0,rendered_ms-video_ms),
                           actual_video_duration_ms=rendered_ms,alignment='single_utterance_actual_waveform_duration')

