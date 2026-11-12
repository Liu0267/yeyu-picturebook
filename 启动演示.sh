#!/usr/bin/env bash
# 页语 · 一键演示启动（Linux/Windows WSL/macOS 通用 bash 版）
# 等价于 启动演示.command；默认演示模式（不启动GPU worker）。
set -eu
cd "$(dirname "$0")"
export YU_DEMO_MODE=1
if [ -f .local/runtime.env ]; then
  set -a; source .local/runtime.env; set +a
fi
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"
export HF_HUB_OFFLINE=1

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || PY=python3

a_pid=""; b_pid=""
cleanup() { [ -n "$a_pid" ] && kill "$a_pid" 2>/dev/null || true; [ -n "$b_pid" ] && kill "$b_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

curl -s -m 2 http://127.0.0.1:8765/v1/health >/dev/null 2>&1 || {
  "$PY" -m uvicorn generation.app:app --host 127.0.0.1 --port 8765 >/tmp/yeyu_a.log 2>&1 &
  a_pid=$!
}
curl -s -m 2 http://127.0.0.1:8801/v1/health >/dev/null 2>&1 || {
  "$PY" -m uvicorn interaction.app:app --host 127.0.0.1 --port 8801 >/tmp/yeyu_b.log 2>&1 &
  b_pid=$!
}

ok_a=n; ok_b=n
for i in $(seq 1 60); do
  curl -s -m 2 http://127.0.0.1:8765/v1/health >/dev/null 2>&1 && ok_a=y
  curl -s -m 2 http://127.0.0.1:8801/v1/health >/dev/null 2>&1 && ok_b=y
  [ "$ok_a" = y ] && [ "$ok_b" = y ] && break
  sleep 1
done
[ "$ok_a" = y ] && [ "$ok_b" = y ] || { echo "启动失败：A端=$ok_a 交互=$ok_b（见 /tmp/yeyu_a.log /tmp/yeyu_b.log）"; exit 1; }

echo "A端API:   http://127.0.0.1:8765/docs"
echo "交互服务: http://127.0.0.1:8801  （Ctrl+C 停止）"
command -v xdg-open >/dev/null && xdg-open "http://127.0.0.1:8801" || open "http://127.0.0.1:8801" 2>/dev/null || true
wait
