#!/bin/zsh
# 页语 · 一键演示启动
# 默认演示模式（YU_DEMO_MODE=1）：只启动A端API与B端交互服务，不启动GPU worker，
# 用于浏览已生成内容、问答、点选与语音演示。生成新视频请另用「启动本地服务.command」。
set -eu
cd "$(dirname "$0")"
export YU_DEMO_MODE=1
if [ -f .local/runtime.env ]; then
  set -a
  source .local/runtime.env
  set +a
fi
export NO_PROXY="${NO_PROXY:+$NO_PROXY,}127.0.0.1,localhost"

yu_python=../../work/.venv/bin/python
[ -x .venv/bin/python ] && yu_python=.venv/bin/python

a_pid=""
b_pid=""
stop_all() { [ -n "$a_pid" ] && kill "$a_pid" 2>/dev/null || true; [ -n "$b_pid" ] && kill "$b_pid" 2>/dev/null || true; }
trap stop_all EXIT INT TERM

# A端 API（8765）：已在运行则复用
if ! curl -s -m 2 http://127.0.0.1:8765/v1/health >/dev/null 2>&1; then
  "$yu_python" -m uvicorn generation.app:app --host 127.0.0.1 --port 8765 >/tmp/yeyu_a.log 2>&1 &
  a_pid=$!
fi

# B端交互服务（8801）：已在运行则复用
if ! curl -s -m 2 http://127.0.0.1:8801/v1/health >/dev/null 2>&1; then
  "$yu_python" -m uvicorn interaction.app:app --host 127.0.0.1 --port 8801 >/tmp/yeyu_b.log 2>&1 &
  b_pid=$!
fi

ok_a=n
ok_b=n
for i in {1..60}; do
  curl -s -m 2 http://127.0.0.1:8765/v1/health >/dev/null 2>&1 && ok_a=y
  curl -s -m 2 http://127.0.0.1:8801/v1/health >/dev/null 2>&1 && ok_b=y
  [ "$ok_a" = y ] && [ "$ok_b" = y ] && break
  sleep 1
done

if [ "$ok_a" != y ] || [ "$ok_b" != y ]; then
  echo "启动失败：A端=$ok_a 交互服务=$ok_b（日志见 /tmp/yeyu_a.log /tmp/yeyu_b.log）"
  exit 1
fi

echo "A端API:   http://127.0.0.1:8765/docs"
echo "交互服务: http://127.0.0.1:8801"
echo "演示界面: http://127.0.0.1:8801  （Ctrl+C 停止本次启动的服务）"
open "http://127.0.0.1:8801"
wait
