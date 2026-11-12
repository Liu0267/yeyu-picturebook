"""Independent HTTP consumer for B handoff. No imports from A internals or model libraries."""
import argparse,hashlib,json,subprocess,tempfile
from pathlib import Path
from urllib.parse import urlparse
import httpx
from PIL import Image,ImageChops

p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:8765');p.add_argument('--book',default='book_mock');p.add_argument('--output',type=Path,default=Path('consumer_report.json'));a=p.parse_args()
if urlparse(a.base_url).hostname not in ('localhost','127.0.0.1','::1'):p.error('This development consumer is restricted to local A services.')
checks=[]
with httpx.Client(base_url=a.base_url,trust_env=False,timeout=60) as c,tempfile.TemporaryDirectory() as folder:
    def get(route):
        r=c.get(route);r.raise_for_status();assert r.headers.get('X-Request-ID');return r
    manifest=get('/v1/books/'+a.book+'/manifest').json()['data']
    published=get('/v1/books/'+a.book+'/clips').json()['data']
    assert published['clips'],'No approved clips; media acceptance is not complete.'
    for clip in published['clips']:
        path=Path(folder)/(clip['clip_id']+'.mp4');media=get('/v1/assets/'+clip['asset_id']);path.write_bytes(media.content)
        sha=hashlib.sha256(media.content).hexdigest();assert media.headers['etag'].strip('"')==sha
        r=c.get('/v1/assets/'+clip['asset_id'],headers={'Range':'bytes=0-31'});assert r.status_code==206 and r.content==media.content[:32]
        pts=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',str(path)]))['frames']
        timestamps=[(i,float(f['best_effort_timestamp_time'])) for i,f in enumerate(pts) if 'best_effort_timestamp_time' in f]
        count=0
        for frame in [f for f in published['frame_evidence'] if f['clip_id']==clip['clip_id']]:
            assert frame['video_sha256']==sha and frame['asset_version']==clip['asset_version']
            image=Path(folder)/(frame['frame_id']+'.png');image.write_bytes(get('/v1/assets/'+frame['image_asset_id']).content)
            i,t=min(timestamps,key=lambda v:abs(v[1]-frame['t_ms']/1000));decoded=Path(folder)/'decoded.png'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(path),'-vf',f'select=eq(n\\,{i})','-frames:v','1',str(decoded)],check=True,timeout=30)
            assert ImageChops.difference(Image.open(image).convert('RGB'),Image.open(decoded).convert('RGB')).getbbox() is None
            count+=1
        checks.append(dict(clip_id=clip['clip_id'],range='passed',media_sha256=sha,actual_frame_pixel_matches=count,is_mock=clip['is_mock']))
    compilation_response=c.get('/v1/books/'+a.book+'/compilations')
    compilations=compilation_response.json()['data']['compilations'] if compilation_response.status_code==200 else []
report=dict(status='passed',consumer='standalone HTTP script with no A-internal imports',book_id=a.book,manifest_revision=manifest['revision'],checks=checks,approved_compilations=len(compilations),
            limitation='A-side contract consumer verification; does not represent B implementation or team acceptance.')
a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False))
