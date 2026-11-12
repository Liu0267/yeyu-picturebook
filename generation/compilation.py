"""Continuous reading video with source-version chapters and actual encoded-frame evidence."""
import hashlib,json,math,re,subprocess,uuid
from pathlib import Path
from .media import probe
from .models import CompilationAsset


def checked_clips(store,items,book_id,db=None):
    book=store.get('book',book_id,db);clips=[]
    for item in items:
        clip=store.get('clip',item['clip_id'],db)
        if not clip or clip['book_id']!=book_id or clip['status']!='approved' or clip['asset_version']!=item['asset_version']:
            raise ValueError('STALE_INPUT')
        page=next(p for p in book['pages'] if p['page_id']==clip['page_id'])
        if page['page_revision']!=clip['page_revision']:raise ValueError('STALE_INPUT')
        if clips and page['sequence']<next(p for p in book['pages'] if p['page_id']==clips[-1]['page_id'])['sequence']:
            raise ValueError('page order must follow source book')
        asset=store.get('asset',clip['asset_id'],db)
        if hashlib.sha256((store.root/asset['path']).read_bytes()).hexdigest()!=asset['sha256']:raise ValueError('media hash mismatch')
        if clip['is_mock']!=book['is_mock']:raise ValueError('mock provenance mismatch')
        if not clip['is_mock'] and clip.get('motion_status')!='passed':raise ValueError('motion quality not accepted')
        clips.append(clip)
    return clips


def stamp(ms):
    h,r=divmod(ms,3600000);m,r=divmod(r,60000);s,ms=divmod(r,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def render_compilation(store,run):
    clips=checked_clips(store,run.payload['items'],run.payload['book_id'])
    out=store.root/'runs'/run.run_id;out.mkdir(parents=True,exist_ok=True)
    _,meta=probe(store.asset_path(clips[0]['asset_id']));v=next(s for s in meta['streams'] if s['codec_type']=='video')
    width,height=v['width'],v['height'];fps=v['avg_frame_rate'];command=['ffmpeg','-nostdin','-v','error','-y'];filters=[];chapters=[];cues=[];offset=0
    def ms(value):
        h,m,s=value.replace(',','.').split(':');return round((int(h)*3600+int(m)*60+float(s))*1000)
    for i,c in enumerate(clips):
        path=store.asset_path(c['asset_id']);duration,metadata=probe(path);command+=['-i',str(path)]
        filters.append(f'[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},setpts=PTS-STARTPTS[v{i}]')
        if any(s['codec_type']=='audio' for s in metadata['streams']):
            filters.append(f'[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=duration={duration/1000},asetpts=PTS-STARTPTS[a{i}]')
        else:filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={duration/1000},asetpts=PTS-STARTPTS[a{i}]')
        if c.get('subtitle_asset_id'):
            text=store.asset_path(c['subtitle_asset_id']).read_text()
            for a,b,line in re.findall(r'(\d\d:\d\d:\d\d[.,]\d{3}) --> (\d\d:\d\d:\d\d[.,]\d{3})\n([^\n]+)',text):
                cues.append((offset+ms(a),min(offset+duration,offset+ms(b)),line))
        chapters.append(dict(clip_id=c['clip_id'],asset_version=c['asset_version'],asset_id=c['asset_id'],
                             video_sha256=store.get('asset',c['asset_id'])['sha256'],page_id=c['page_id'],page_revision=c['page_revision'],
                             start_ms=offset,end_ms=offset+duration));offset+=duration
    subtitle=out/'subtitles.srt';subtitle.write_text('\n\n'.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n{text}' for i,(a,b,text) in enumerate(cues,1))+'\n')
    vtt=out/'subtitles.vtt';vtt.write_text('WEBVTT\n\n'+'\n\n'.join(f'{stamp(a).replace(",",".")} --> {stamp(b).replace(",",".")}\n{text}' for a,b,text in cues)+'\n')
    if cues:command+=['-i',str(subtitle)]
    filters.append(''.join(f'[v{i}][a{i}]' for i in range(len(clips)))+f'concat=n={len(clips)}:v=1:a=1[v][a]')
    target=out/'reading.mp4'
    command+=['-filter_complex',';'.join(filters),'-map','[v]','-map','[a]',*(['-map',f'{len(clips)}:0'] if cues else []),'-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-c:s','mov_text','-t',str(offset/1000),'-movflags','+faststart',str(target)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=300)
    (out/'render.log').write_text(result.stderr)
    if result.returncode:raise RuntimeError('compilation render failed')
    # Recheck dependencies after rendering. A concurrent edit must never publish stale content.
    checked_clips(store,run.payload['items'],run.payload['book_id'])
    actual,_=probe(target)
    record=CompilationAsset(compilation_id='compilation_'+uuid.uuid4().hex,book_id=run.payload['book_id'],asset_version=1,
                            asset_id=store.asset(target,'video/mp4',run.is_mock),subtitle_asset_id=store.asset(vtt,'text/vtt',run.is_mock),
                            duration_ms=actual,chapters=chapters,status='needs_review',run_id=run.run_id,is_mock=run.is_mock)
    timestamps=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',str(target)]))['frames']
    candidates=[(i,float(f['best_effort_timestamp_time'])) for i,f in enumerate(timestamps) if 'best_effort_timestamp_time' in f]
    chosen=sorted({min(candidates,key=lambda f:abs(f[1]-t/1000)) for ch in chapters for t in [ch['start_ms'],(ch['start_ms']+ch['end_ms'])//2,ch['end_ms']-100]})
    evidence=[];sha=store.get('asset',record.asset_id)['sha256']
    for n,(index,pts) in enumerate(chosen,1):
        t=math.ceil(pts*1000-1e-6);ch=next((c for c in chapters if c['start_ms']<=t<c['end_ms']),chapters[-1])
        image=out/f'frame_{t:06}.png'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(target),'-vf',f'select=eq(n\\,{index})','-frames:v','1',str(image)],check=True,timeout=30)
        frame=dict(frame_id=f'{record.compilation_id}_f{n}',compilation_id=record.compilation_id,asset_version=1,t_ms=t,
                   source_clip_id=ch['clip_id'],source_t_ms=t-ch['start_ms'],image_asset_id=store.asset(image,'image/png',run.is_mock),
                   video_sha256=sha,status='needs_review',visual_claims=[],is_mock=run.is_mock)
        evidence.append(frame)
    with store.connect() as db:
        checked_clips(store,run.payload['items'],run.payload['book_id'],db)
        store.put('compilation',record.compilation_id,record,db)
        for frame in evidence:store.put('compilation_frame',frame['frame_id'],frame,db)
    return record,evidence
