#!/bin/zsh
set -eu
unsetopt BG_NICE
cd "$(dirname "$0")"
if [ -f .local/runtime.env ]; then
  set -a
  source .local/runtime.env
  set +a
fi
if [ -x .venv/bin/python ]; then
  yu_python=.venv/bin/python
elif [ -x ../../work/.venv/bin/python ]; then
  yu_python=../../work/.venv/bin/python
else
  echo '请先按 README_generation.md 安装 .venv 与依赖。'
  exit 1
fi
if [ ! -f data/state.sqlite ]; then
  "$yu_python" scripts/restore_snapshot.py
fi
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"
"$yu_python" -m generation.workflow &
yu_worker_pid=$!
trap 'kill "$yu_worker_pid" 2>/dev/null || true' EXIT INT TERM
"$yu_python" -m uvicorn generation.app:app --host 127.0.0.1 --port 8765
