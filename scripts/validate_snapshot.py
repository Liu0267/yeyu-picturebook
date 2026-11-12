import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generation.store import Store
from generation.models import BookManifest, ClipAsset, FrameEvidence, RunRecord, ShotSpec,NarrationAsset,PageObservation
from generation.media import probe


def validate(root):
    store=Store(root)
    counts={}
    for kind,model in [('book',BookManifest),('clip',ClipAsset),('frame',FrameEvidence),('run',RunRecord),('shot',ShotSpec),('narration',NarrationAsset)]:
        records=store.all(kind)
        for record in records:model.model_validate(record)
        counts[kind]=len(records)
    for asset in store.all('asset'):
        path=store.asset_path(asset['asset_id'])
        assert hashlib.sha256(path.read_bytes()).hexdigest()==asset['sha256'], asset['asset_id']
    for book in store.all('book'):
        for page in book['pages']:store.asset_path(page['image_asset_id'])
    for shot in store.all('shot'):
        book=store.get('book',shot['book_id'])
        assert book and shot['page_id'] in {p['page_id'] for p in book['pages']}
        assert set(shot['fact_ids'])<={f['fact_id'] for f in book['facts']}
        assert set(shot['entity_ids'])<={e['entity_id'] for e in book['entities']}
        store.asset_path(shot['reference_asset_id'])
    for narration in store.all('narration'):
        book=store.get('book',narration['book_id'])
        assert book and narration['page_id'] in {p['page_id'] for p in book['pages']}
        store.asset_path(narration['audio_asset_id'])
    for observation in store.all('understanding'):
        if observation['schema_status']=='passed':PageObservation.model_validate(observation['parsed'])
    counts['understanding']=len(store.all('understanding'))
    for clip in store.all('clip'):
        duration,_=probe(store.asset_path(clip['asset_id']))
        assert abs(duration-clip['duration_ms'])<=50
    for frame in store.all('frame'):
        clip=store.get('clip',frame['clip_id'])
        assert clip and clip['asset_version']==frame['asset_version']
        assert 0<=frame['t_ms']<clip['duration_ms']
        assert store.get('asset',clip['asset_id'])['sha256']==frame['video_sha256']
    return dict(status='passed',counts=counts,asset_count=len(store.all('asset')),
                checks=['schema','source and entity references','asset SHA256','video duration','frame timestamp and video hash'])


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    result=validate(root/'data')
    (root/'docs/snapshot_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
