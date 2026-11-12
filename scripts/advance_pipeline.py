"""Idempotent local CLI. Re-run to advance; never auto-review frames or re-submit failed runs."""
import argparse,json
import httpx
from urllib.parse import urlparse
p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:8765');p.add_argument('--shot',required=True);p.add_argument('--pipeline-id',required=True);p.add_argument('--resume-video');p.add_argument('--resume-narration');a=p.parse_args()
if urlparse(a.base_url).hostname not in ('127.0.0.1','localhost','::1'):p.error('This CLI only connects to a local A service.')
resume={k:v for k,v in [('video_generation',a.resume_video),('narration_render',a.resume_narration)] if v}
with httpx.Client(base_url=a.base_url,trust_env=False,timeout=30) as c:
 r=c.post('/v1/pipelines',json=dict(shot_id=a.shot,idempotency_key=a.pipeline_id,resume_run_ids=resume));r.raise_for_status()
 r=c.post('/v1/pipelines/'+a.pipeline_id+'/advance');r.raise_for_status();print(json.dumps(r.json(),ensure_ascii=False,indent=2))
