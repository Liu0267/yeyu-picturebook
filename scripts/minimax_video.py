#!/usr/bin/env python3
"""MiniMax 视频生成最小适配器：submit → poll → download。

对应 V2.1 §9.3 的四条硬规矩：
  1. 幂等优先   —— 同一 business_id 只允许创建一个付费任务
  2. 先写盘再解析 —— POST 一返回就落 task_id，避免"提交成功但进程崩了"
  3. 提交不明不重建 —— 超时/断网进入 submission_unknown，只查询，不重试创建
  4. 失败要吵   —— 无人值守时弹系统通知，否则白等一晚

用法:
    export MINIMAX_API_KEY=sk-...
    python scripts/minimax_video.py submit --business-id CN01-S01 --prompt "..." --duration 6 --dry-run
    python scripts/minimax_video.py submit --business-id CN01-S01 --prompt "..."
    python scripts/minimax_video.py watch          # 挂机轮询，失败即告警
    python scripts/minimax_video.py status

首次接入必做: 先 `submit --dry-run` 看请求体，再真跑一条 4 秒任务，
用 `--dump-raw` 打印原始响应，把字段名核准后再批量提交。
文档: https://platform.minimax.cn/docs/api-reference/video-generation-v2-create
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "var" / "video_tasks.json"
OUT = ROOT / "var" / "video_out"
BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.cn").rstrip("/")
KEY = os.environ.get("MINIMAX_API_KEY", "")

# 单价（元/输出秒），与 V2.1 §9.5 一致；改价只改这里
PRICE = {"480P": 0.16, "768P": 0.50, "2K": 0.80}

# 合法组合：Lite 只能 480P/768P，H3 只能 768P/2K（V2.1 §9.1）
ALLOWED = {
    ("MiniMax-H3-Lite", "480P"), ("MiniMax-H3-Lite", "768P"),
    ("MiniMax-H3", "768P"), ("MiniMax-H3", "2K"),
}

DONE = {"Success", "success", "ready"}
ALIVE = {"submitted", "generating", "downloading", "validating", "Preparing", "Queueing", "Processing"}


# ---------------------------------------------------------------- 账本

def _load() -> dict[str, dict]:
    if not LEDGER.exists():
        return {}
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _save(data: dict[str, dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(LEDGER)  # 原子替换，半截文件不会污染账本


def _notify(msg: str) -> None:
    """macOS 通知；其他平台退化为 stderr。"""
    print(f"[告警] {msg}", file=sys.stderr, flush=True)
    if sys.platform != "darwin":
        return
    script = f'display notification {json.dumps(msg)} with title "页语·生成" sound name "Basso"'
    subprocess.run(["osascript", "-e", script], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- HTTP

def _call(method: str, path: str, payload: dict | None = None, timeout: int = 60,
          dump_raw: bool = False) -> dict:
    if not KEY:
        raise SystemExit("缺少 MINIMAX_API_KEY 环境变量")
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    if dump_raw or os.environ.get("MINIMAX_DUMP"):
        print(raw, file=sys.stderr)
        raw_dir = ROOT / "var" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        tag = re.sub(r"[^A-Za-z0-9]+", "_", path)[:40].strip("_")
        (raw_dir / f"{stamp}_{method}_{tag}.json").write_text(raw, encoding="utf-8")
    body = json.loads(raw)
    code = _pick(body, "base_resp", "status_code")
    if code not in (None, 0):
        raise RuntimeError(f"业务错误 {code}: {_pick(body, 'base_resp', 'status_msg')}")
    return body


def _pick(body: dict, *path: str) -> Any:
    """从顶层或 task/data 嵌套里取字段——供应商两种包装都见过。"""
    for holder in (body, body.get("task") or {}, body.get("data") or {}):
        cur: Any = holder
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                cur = None
                break
            cur = cur[k]
        if cur is not None:
            return cur
    return None


# ---------------------------------------------------------------- 提交

@dataclass
class Spec:
    business_id: str
    prompt: str
    duration: int = 6
    model: str = "MiniMax-H3-Lite"
    resolution: str = "480P"
    ratio: str = "16:9"
    first_frame_image: str | None = None
    generate_audio: bool = False
    shot: str = ""
    extra: dict = field(default_factory=dict)

    def payload(self) -> dict:
        p = {
            "model": self.model,
            "prompt": self.prompt,
            "duration": int(self.duration),
            "resolution": self.resolution.upper(),
            "ratio": self.ratio,
            "generate_audio": self.generate_audio,
        }
        if self.first_frame_image:
            p["first_frame_image"] = self.first_frame_image
        p.update(self.extra)
        return p

    def fingerprint(self) -> str:
        """同参数同结果：参数变了就是一个新任务。"""
        blob = json.dumps(self.payload(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def cost(self) -> float:
        return round(self.duration * PRICE.get(self.resolution.upper(), 0.0), 2)


def submit(spec: Spec, dry_run: bool = False, dump_raw: bool = False,
           max_cny: float | None = None) -> dict:
    ledger = _load()
    rec = ledger.get(spec.business_id)

    # 幂等闸门 1：已有有效任务，直接返回，绝不再花钱
    if rec and rec.get("status") not in {"failed", "canceled"}:
        print(f"[幂等] {spec.business_id} 已存在 task_id={rec.get('task_id')} "
              f"status={rec.get('status')}，未重复提交")
        return rec

    # 幂等闸门 2：上次提交结果不明，先查再说
    if rec and rec.get("status") == "submission_unknown" and rec.get("task_id"):
        print(f"[恢复] {spec.business_id} 上次提交状态不明，先查询 task_id={rec['task_id']}")
        return refresh(spec.business_id, dump_raw)

    payload = spec.payload()
    if dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print(f"# 预计费用 {spec.cost()} 元（{spec.duration}s × {PRICE.get(spec.resolution.upper(), 0)}）")
        return {}

    if max_cny is not None:
        spent = sum(r.get("cost", 0) for r in ledger.values())
        if spent + spec.cost() > max_cny:
            raise SystemExit(f"预算熔断：已花 {spent:.2f} 元，本次 {spec.cost()} 元，上限 {max_cny} 元")

    ledger.setdefault(spec.business_id, {}).update({
        "business_id": spec.business_id,
        "shot": spec.shot,
        "fingerprint": spec.fingerprint(),
        "request": payload,
        "status": "submitting",
        "cost": spec.cost(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _save(ledger)

    try:
        body = _call("POST", "/v2/video_generation", payload, dump_raw=dump_raw)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # 关键分支：请求可能已经到达供应商，绝不能重发
        ledger[spec.business_id].update({"status": "submission_unknown", "error": str(exc)})
        _save(ledger)
        _notify(f"{spec.business_id} 提交状态不明（{exc}），已停止重试，请人工核对")
        return ledger[spec.business_id]
    except RuntimeError as exc:
        ledger[spec.business_id].update({"status": "failed", "error": str(exc)})
        _save(ledger)
        _notify(f"{spec.business_id} 提交失败：{exc}")
        raise

    task_id = _pick(body, "task_id")
    if not task_id:
        ledger[spec.business_id].update({"status": "failed", "error": "响应中没有 task_id"})
        _save(ledger)
        _notify(f"{spec.business_id} 提交无 task_id，原始响应需人工检查")
        raise RuntimeError(f"未取到 task_id，原始响应：{json.dumps(body, ensure_ascii=False)[:500]}")

    ledger[spec.business_id].update({
        "task_id": task_id,
        "status": "submitted",
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "raw_submit": body if dump_raw else None,
    })
    _save(ledger)
    print(f"[提交] {spec.business_id} → task_id={task_id}（{spec.cost()} 元）")
    return ledger[spec.business_id]


# ---------------------------------------------------------------- 查询与下载

def refresh(business_id: str, dump_raw: bool = False) -> dict:
    ledger = _load()
    rec = ledger[business_id]
    task_id = rec.get("task_id")
    if not task_id:
        raise SystemExit(f"{business_id} 没有 task_id，无法查询")

    body = _call("GET", f"/v2/query/video_generation?task_id={task_id}", dump_raw=dump_raw)
    status = _pick(body, "status") or "unknown"
    file_id = _pick(body, "file_id")

    if status in DONE and file_id:
        rec.update({"status": "downloading", "file_id": file_id})
        _save(ledger)
        path = _download(business_id, file_id)
        rec.update({"status": "ready", "file": str(path.relative_to(ROOT)),
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        _save(ledger)
        print(f"[完成] {business_id} → {rec['file']}（{rec['bytes']} 字节）")
        return rec

    if status and status not in ALIVE and status not in DONE:
        rec.update({"status": "failed", "error": f"远端状态 {status}"})
        _save(ledger)
        _notify(f"{business_id} 生成失败：{status}")
        return rec

    rec.update({"status": "generating", "remote_status": status,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    _save(ledger)
    return rec


def _download(business_id: str, file_id: str) -> Path:
    body = _call("GET", f"/v1/files/retrieve?file_id={file_id}")
    url = _pick(body, "download_url") or _pick(body, "file", "download_url")
    if not url:
        raise RuntimeError(f"{business_id} 未取到 download_url")
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"{business_id}.mp4"
    tmp = dest.with_suffix(".part")  # 先下到临时名，成功再改名
    with urllib.request.urlopen(url, timeout=300) as resp, open(tmp, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    if tmp.stat().st_size == 0:
        raise RuntimeError(f"{business_id} 下载到空文件")
    tmp.replace(dest)
    return dest


# ---------------------------------------------------------------- 挂机

def watch(max_cny: float | None = None, interval: float = 20.0,
          deadline_hours: float = 8.0) -> None:
    end = time.time() + deadline_hours * 3600
    backoff = interval
    while time.time() < end:
        ledger = _load()
        pending = [k for k, v in ledger.items()
                   if v.get("task_id") and v.get("status") in {"submitted", "generating", "downloading"}]
        if not pending:
            left = [k for k, v in ledger.items() if v.get("status") == "submission_unknown"]
            if left:
                _notify(f"仍有 {len(left)} 个任务提交状态不明，需人工核对：" + ", ".join(left[:3]))
            print("[挂机结束] 没有待处理任务")
            return
        for bid in pending:
            try:
                rec = refresh(bid)
                print(f"  {bid}: {rec['status']}")
            except Exception as exc:                     # 单条失败不影响其他任务
                print(f"  {bid}: 查询出错 {exc}", file=sys.stderr)
        if max_cny is not None:
            spent = sum(v.get("cost", 0) for v in _load().values())
            if spent > max_cny:
                _notify(f"预算熔断：已花 {spent:.2f} 元 > {max_cny} 元")
                return
        time.sleep(backoff + random.uniform(0, 3))        # 抖动，避免固定节奏
        backoff = min(backoff * 1.3, 120)


# ---------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="MiniMax 视频最小适配器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="提交一条生成任务（幂等）")
    s.add_argument("--business-id", required=True)
    s.add_argument("--prompt", required=True)
    s.add_argument("--duration", type=int, default=6, help="4—15 秒")
    s.add_argument("--model", default="MiniMax-H3-Lite")
    s.add_argument("--resolution", default="480P", choices=["480P", "768P", "2K"])
    s.add_argument("--ratio", default="16:9")
    s.add_argument("--first-frame", default=None)
    s.add_argument("--shot", default="")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--dump-raw", action="store_true", help="打印并把原始响应存到 var/raw/")
    s.add_argument("--max-cny", type=float, default=None)

    w = sub.add_parser("watch", help="挂机轮询，失败告警")
    w.add_argument("--max-cny", type=float, default=None)
    w.add_argument("--interval", type=float, default=20.0)
    w.add_argument("--deadline-hours", type=float, default=8.0)

    p = sub.add_parser("poll", help="查一轮")
    p.add_argument("--business-id", default=None)
    p.add_argument("--dump-raw", action="store_true")

    sub.add_parser("status", help="打印账本")

    a = ap.parse_args()

    if a.cmd == "submit":
        if not 4 <= a.duration <= 15:
            raise SystemExit("duration 必须在 4—15 秒之间")
        if (a.model, a.resolution.upper()) not in ALLOWED:
            raise SystemExit(f"不支持的组合 {a.model} + {a.resolution}；"
                             f"合法组合：{sorted(ALLOWED)}")
        spec = Spec(business_id=a.business_id, prompt=a.prompt, duration=a.duration,
                    model=a.model, resolution=a.resolution, ratio=a.ratio,
                    first_frame_image=a.first_frame, shot=a.shot)
        rec = submit(spec, dry_run=a.dry_run, dump_raw=a.dump_raw, max_cny=a.max_cny)
        return 0 if rec else 0

    if a.cmd == "poll":
        ledger = _load()
        ids = [a.business_id] if a.business_id else [
            k for k, v in ledger.items() if v.get("task_id") and v.get("status") != "ready"]
        for bid in ids:
            rec = refresh(bid, dump_raw=a.dump_raw)
            print(f"{bid}: {rec['status']} {rec.get('remote_status', '')}")
        return 0

    if a.cmd == "watch":
        watch(max_cny=a.max_cny, interval=a.interval, deadline_hours=a.deadline_hours)
        return 0

    if a.cmd == "status":
        ledger = _load()
        spent = sum(v.get("cost", 0) for v in ledger.values())
        for bid, v in sorted(ledger.items()):
            print(f"{bid:24s} {v.get('status','?'):20s} {v.get('cost',0):6.2f}元  {v.get('task_id','')}")
        print(f"\n合计 {len(ledger)} 条，已记 {spent:.2f} 元")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
