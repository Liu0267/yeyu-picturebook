#!/usr/bin/env python3
"""触控基准：在已审帧claims区域框内/外采样，统计L1命中率与拒识正确率。

口径（B端手册B10）：
- 正样本：帧claims区域框内随机点 → 期望 resolve_touch 命中任一对象
- 负样本：远离所有对象框的空白点（每帧随机生成并过滤）→ 期望返回None
- 歧义：多对象重叠取最小面积属设计行为，计为命中
输出：evaluation/interaction/touch_results.json
"""

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interaction import sync, touch as touch_mod  # noqa: E402


def sample_points(db, n_inside=60, n_outside=40, seed=42):
    rng = random.Random(seed)
    frames = [dict(r) for r in db.execute(
        "SELECT f.* FROM frames f JOIN objects o ON o.clip_id=f.clip_id AND o.asset_version=f.asset_version"
        " WHERE f.status='reviewed' GROUP BY f.frame_id")]
    cases = []
    for fr in frames:
        objs = [dict(r) for r in db.execute(
            'SELECT * FROM objects WHERE clip_id=? AND asset_version=? AND t_ms=?',
            (fr['clip_id'], fr['asset_version'], fr['t_ms']))]
        boxes = []
        for o in objs:
            if o['bbox']:
                boxes.append((o, json.loads(o['bbox'])))
        if not boxes:
            continue
        # 正样本：框内点（避开边缘5%）
        for _ in range(max(1, n_inside // len(frames))):
            o, b = rng.choice(boxes)
            x1, y1, x2, y2 = b
            if x2 - x1 < 0.1 or y2 - y1 < 0.1:
                continue
            cases.append(dict(frame_id=fr['frame_id'], clip_id=fr['clip_id'],
                              asset_version=fr['asset_version'], t_ms=fr['t_ms'],
                              x=round(rng.uniform(x1 + 0.05 * (x2 - x1), x2 - 0.05 * (x2 - x1)), 4),
                              y=round(rng.uniform(y1 + 0.05 * (y2 - y1), y2 - 0.05 * (y2 - y1)), 4),
                              expect='hit'))
        # 负样本：所有框外的点
        for _ in range(max(1, n_outside // len(frames))):
            for _try in range(20):
                x, y = round(rng.uniform(0.02, 0.98), 4), round(rng.uniform(0.02, 0.98), 4)
                if all(not (b[0] <= x <= b[2] and b[1] <= y <= b[3]) for _, b in boxes):
                    cases.append(dict(frame_id=fr['frame_id'], clip_id=fr['clip_id'],
                                      asset_version=fr['asset_version'], t_ms=fr['t_ms'],
                                      x=x, y=y, expect='miss'))
                    break
    return cases


def main():
    db = sync.connect()
    cases = sample_points(db)
    rows = []
    for c in cases:
        obj = touch_mod.resolve_touch(db, c['x'], c['y'], c['clip_id'], c['asset_version'], c['t_ms'])
        got = 'hit' if obj else 'miss'
        rows.append(dict(**c, got=got, passed=(got == c['expect']),
                         name=obj.get('name') if obj else None))
    total = len(rows)
    passed = sum(1 for r in rows if r['passed'])
    inside = [r for r in rows if r['expect'] == 'hit']
    outside = [r for r in rows if r['expect'] == 'miss']
    out = dict(
        total=total, passed=passed, accuracy=round(passed / total, 4) if total else 0,
        inside=dict(n=len(inside), hit=sum(1 for r in inside if r['passed']),
                    rate=round(sum(1 for r in inside if r['passed']) / max(1, len(inside)), 4)),
        outside=dict(n=len(outside), reject=sum(1 for r in outside if r['passed']),
                     rate=round(sum(1 for r in outside if r['passed']) / max(1, len(outside)), 4)),
        rows=rows)
    out_path = ROOT / 'evaluation' / 'interaction' / 'touch_results.json'
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf8')
    print(f"触控基准：{passed}/{total} = {out['accuracy']:.2%}")
    print(f"  框内命中: {out['inside']['hit']}/{out['inside']['n']} ({out['inside']['rate']:.2%})")
    print(f"  空白拒识: {out['outside']['reject']}/{out['outside']['n']} ({out['outside']['rate']:.2%})")
    fails = [r for r in rows if not r['passed']]
    for r in fails[:6]:
        print(f"  FAIL: ({r['x']},{r['y']})@t={r['t_ms']} 期望{r['expect']} 得到{r['got']}")


if __name__ == '__main__':
    main()
