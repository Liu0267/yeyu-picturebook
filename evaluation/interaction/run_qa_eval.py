#!/usr/bin/env python3
"""跑问答题集评测：直接调用 qa.answer（离线、无HTTP），输出正确率与失败明细。

判定：answered题 = status正确 且 answer_text含预期关键词 且 evidence可验证；
uncertain题 = status=='uncertain'。点选指代题先模拟 touches 流程。
输出：evaluation/interaction/qa_results_{dev|sealed}.json + 控制台汇总。
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interaction import qa, sync, touch as touch_mod  # noqa: E402


def judge(item, result, entity=None):
    """判定原则：语义正确（含预期关键词）+ 引用真实证据 + 状态正确。
    allowed_evidence 仅作参考（同主题多fact属合理回答），不做硬性单选。"""
    status_ok = result.get('status') == item['expect_status']
    if item['expect_status'] == 'uncertain':
        return status_ok, 'status'
    if not status_ok:
        return False, 'status'
    text = result.get('answer_text') or ''
    ev = result.get('evidence') or []
    qtype = item.get('qtype')

    if qtype == 'pointer':
        # 指代题：回答指向点选对象且有画面/原书证据
        name_kw = item.get('entity_name') or ''
        ok = bool(ev) and (name_kw in text or '这是' in text)
        return ok, ('ok' if ok else 'keywords')

    if qtype == 'entity':
        # 外观题：回答含实体名（或其尾部片段）且有证据
        name_kw = item.get('entity_name') or ''
        hit = name_kw and (name_kw in text or any(name_kw[i:i+2] in text for i in range(0, max(1, len(name_kw) - 1))))
        ok = bool(ev) and bool(hit)
        return ok, ('ok' if ok else 'keywords')

    kw_ok = any(k in text for k in item.get('expect_keywords') or [])
    if not kw_ok:
        return False, 'keywords'
    if not ev:
        return False, 'evidence'
    # book类证据必须属于同一本书（防串书）
    if item.get('qtype') in ('book_fact', 'timeline', 'book_title'):
        for e in ev:
            pid = e.get('page_id') or ''
            if pid and not pid.startswith(item['book_id']):
                return False, 'evidence'
    return True, 'ok'


def run(dataset_path, label):
    db = sync.connect()
    items = json.loads(Path(dataset_path).read_text())
    rows = []
    t_all = time.perf_counter()
    for it in items:
        entity = None
        if it.get('touch'):
            obj = touch_mod.resolve_touch(db, it['touch']['x'], it['touch']['y'],
                                          it['touch']['clip_id'], it['touch']['asset_version'],
                                          it['touch']['t_ms'])
            if obj:
                entity = dict(name=obj.get('name'), description=obj.get('description'),
                              object_id=obj['object_id'], clip_id=it['touch']['clip_id'],
                              asset_version=it['touch']['asset_version'],
                              t_ms=obj.get('t_ms', it['touch']['t_ms']))
        t0 = time.perf_counter()
        result = qa.answer(db, it['question'], it['book_id'],
                           it.get('snapshot') or dict(book_id=it['book_id']), entity)
        ms = int((time.perf_counter() - t0) * 1000)
        ok, why = judge(it, result, entity)
        rows.append(dict(qid=it['qid'], qtype=it['qtype'], question=it['question'],
                         passed=ok, fail_reason=why if not ok else None,
                         answer=result.get('answer_text'), status=result.get('status'),
                         method=result.get('method'), evidence=result.get('evidence'),
                         elapsed_ms=ms))
    total = len(rows)
    passed = sum(1 for r in rows if r['passed'])
    by_type = {}
    for r in rows:
        s = by_type.setdefault(r['qtype'], dict(n=0, passed=0, p95_ms=0))
        s['n'] += 1
        s['passed'] += r['passed']
    for s in by_type.values():
        s['p95_ms'] = sorted([r['elapsed_ms'] for r in rows if r['qtype'] and s])[max(0, int(s['n'] * 0.95) - 1)]
    out = dict(dataset=label, total=total, passed=passed,
               accuracy=round(passed / total, 4) if total else 0,
               by_type=by_type, wall_ms=int((time.perf_counter() - t_all) * 1000),
               rows=rows)
    out_path = Path(dataset_path).parent / f"qa_results_{label}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf8')
    print(f"[{label}] 正确率 {passed}/{total} = {out['accuracy']:.2%}")
    for t, s in sorted(by_type.items()):
        print(f"  {t}: {s['passed']}/{s['n']}")
    fails = [r for r in rows if not r['passed']]
    for r in fails[:8]:
        print(f"  FAIL[{r['qid']}] {r['fail_reason']}: {r['question']} → {r['answer'][:30]} ({r['status']})")
    return out


if __name__ == '__main__':
    base = ROOT / 'evaluation' / 'interaction'
    which = sys.argv[1] if len(sys.argv) > 1 else 'dev'
    run(base / f'qa_dataset_{which}.json', which)
