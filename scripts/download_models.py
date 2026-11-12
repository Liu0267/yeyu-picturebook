import argparse
from pathlib import Path
from huggingface_hub import snapshot_download

parser=argparse.ArgumentParser()
parser.add_argument('--directory',required=True)
args=parser.parse_args()
root=Path(args.directory).resolve()
for repo,name in [('mlx-community/Qwen3-VL-4B-Instruct-4bit','Qwen3-VL-4B-Instruct-4bit'),
                  ('mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-4bit','Qwen3-TTS-1.7B-CustomVoice-4bit')]:
    snapshot_download(repo,local_dir=root/name,token=False,max_workers=3)
    print('Ready:',repo,flush=True)
