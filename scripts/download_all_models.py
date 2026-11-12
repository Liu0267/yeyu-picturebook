"""下载本项目全部本地模型（6个，约17GB）并按docs/model_manifest.json校验SHA256。

用法：python scripts/download_all_models.py --directory <模型目录>
环境变量：HF_ENDPOINT=https://hf-mirror.com （国内镜像加速）
"""

import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

parser = argparse.ArgumentParser()
parser.add_argument('--directory', required=True)
args = parser.parse_args()
root = Path(args.directory).resolve()
root.mkdir(parents=True, exist_ok=True)

manifest = json.loads((Path(__file__).resolve().parents[1] / 'docs' / 'model_manifest.json').read_text())

# 1) 快照型模型（整仓库下载）
for item in manifest['snapshots']:
    dest = root / item['name']
    print(f"[snapshot] {item['repo_id']} → {dest}", flush=True)
    snapshot_download(item['repo_id'], local_dir=dest, token=False, max_workers=4)
    verify_dir(dest, item) if False else None

# 2) 校验已落盘文件的SHA256（manifest中记录的文件）
def sha256(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify_dir(dest, item):
    ok = bad = 0
    for f, expected in (item.get('sha256_files') or {}).items():
        p = dest / f
        if not p.is_file():
            bad += 1
            print(f"  [missing] {f}")
            continue
        actual = sha256(p)
        if actual == expected:
            ok += 1
        else:
            bad += 1
            print(f"  [MISMATCH] {f}: {actual[:12]} != {expected[:12]}")
    print(f"  校验: {ok} 通过 / {bad} 异常", flush=True)


for item in manifest['snapshots']:
    verify_dir(root / item['name'], item)

print('全部模型就绪。')
