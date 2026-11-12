"""Restore shipped, reviewed snapshot only into an empty database; do not overwrite edits."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generation.store import Store
from generation.models import BookManifest, ClipAsset, FrameEvidence, RunRecord, ShotSpec,NarrationAsset


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',default=str(Path(__file__).resolve().parents[1]/'data'))
    args=parser.parse_args()
    store=Store(args.data_dir)
    if store.all('book') or store.all('run'):
        raise SystemExit('Database is not empty; existing edits were preserved.')
    records=json.loads((store.root/'seed_state.json').read_text())
    models={'book':BookManifest,'clip':ClipAsset,'frame':FrameEvidence,'run':RunRecord,'shot':ShotSpec,'narration':NarrationAsset}
    for kind, key, data in records:
        if kind in models:models[kind].model_validate(data)
        if kind=='asset':
            path=(store.root/data['path']).resolve()
            path.relative_to(store.root)
            import hashlib
            if hashlib.sha256(path.read_bytes()).hexdigest()!=data['sha256']:
                raise SystemExit(f'Asset hash mismatch: {key}')
    with store.connect() as db:
        for kind,key,data in records:store.put(kind,key,data,db)
    print(f'Restored {len(records)} snapshot records, with asset hash verification.')
