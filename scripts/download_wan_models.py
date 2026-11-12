"""Download pinned public model files; never reads or uploads book material."""
import argparse
import hashlib
import json
from pathlib import Path
import httpx2
from huggingface_hub import hf_hub_download

parser=argparse.ArgumentParser()
parser.add_argument('--directory',required=True)
args=parser.parse_args();target=Path(args.directory);target.mkdir(parents=True,exist_ok=True)
manifest=json.loads((Path(__file__).resolve().parents[1]/'docs/wan_model_manifest.json').read_text())
for item in manifest['huggingface']:
    path=Path(hf_hub_download(item['repo_id'],item['filename'],revision=item['revision'],local_dir=target,token=False))
    assert hashlib.file_digest(path.open('rb'),'sha256').hexdigest()==item['sha256'],item['filename']
    print('Verified:',item['filename'],flush=True)
item=manifest['taehv'];path=target/item['filename']
if not path.is_file():
    with httpx2.Client(follow_redirects=True,timeout=120) as client:
        with client.stream('GET',item['url']) as response:
            response.raise_for_status()
            with path.with_suffix('.part').open('wb') as file:
                for chunk in response.iter_bytes():file.write(chunk)
    path.with_suffix('.part').replace(path)
assert hashlib.file_digest(path.open('rb'),'sha256').hexdigest()==item['sha256'],item['filename']
(target/'verified_manifest.json').write_text(json.dumps(manifest,indent=2))
print('Verified:',item['filename'],flush=True)
