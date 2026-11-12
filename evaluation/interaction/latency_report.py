#!/usr/bin/env python3
"""延迟报告：汇总问答/触控评测的实际耗时（P50/P95），口径与手册B10一致。

说明：P0问答为本地检索（预期<50ms）；端到端语音链路（录音→ASR→检索→播报）
的分段耗时在演示设备上以 /v1/audio 的 asr_elapsed_ms + qa elapsed_ms 记录。
加载动画/占位提示不计入有效回答时间。
"""

import json
import statistics
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def pctl(vals, p):
    vals = sorted(vals)
    if not vals:
        return 0
    idx = max(0, int(len(vals) * p) - 1)
    return vals[idx]


def main():
    report = dict(generated_at=__import__('time').strftime('%Y-%m-%dT%H:%M:%S'), segments={})
    for name in ('qa_results_dev.json', 'qa_results_sealed.json'):
        p = BASE / name
        if not p.is_file():
            continue
        data = json.loads(p.read_text())
        ms = [r['elapsed_ms'] for r in data['rows']]
        asr = [r['asr_elapsed_ms'] for r in data['rows'] if r.get('asr_elapsed_ms')]
        report['segments'][name.replace('.json', '')] = dict(
            n=data['total'], accuracy=data['accuracy'],
            qa_ms=dict(p50=statistics.median(ms), p95=pctl(ms, .95), max=max(ms)),
            **({'asr_ms': dict(p50=statistics.median(asr), p95=pctl(asr, .95))} if asr else {}))
    tp = BASE / 'touch_results.json'
    if tp.is_file():
        t = json.loads(tp.read_text())
        report['segments']['touch'] = dict(n=t['total'], accuracy=t['accuracy'],
                                           inside_rate=t['inside']['rate'],
                                           outside_rate=t['outside']['rate'])
    out = BASE / 'latency_report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
