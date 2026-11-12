#!/usr/bin/env python3
"""自动构建问答自测题集（基于A端manifest/帧证据，人工校验字段预留）。

题型分布（目标120题，开发80/封存40）：
- 原书事实（facts → "书里说…"预期）
- 外观/实体（entities）
- 书名（books.title 直达规则）
- 时间顺序（events order）
- 视频画面（reviewed帧claims → 画面题预期）
- 不可回答（固定模板，预期 uncertain）
- 点选指代（模拟 touches → "它是谁"）

判定口径：
- answered 题：answer_text 含 ≥1 个预期关键词 且 evidence 非空 且 evidence.id 在允许集合
- uncertain 题：status == 'uncertain'
输出：evaluation/interaction/qa_dataset_dev.json / qa_dataset_sealed.json
"""

import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from interaction import sync, indexer  # noqa: E402

STOP = set('的了是在有和与就都也很我你他她它这本书画面里说写着什么怎么为什么谁哪里一个一只第一次着着上中下前后左右'.split())


def keywords(text, n=2):
    words = re.findall(r'[\u4e00-\u9fff]{2,6}', text or '')
    words = [w for w in words if not any(c in STOP for c in w) and w not in STOP]
    return words[:n] or (re.findall(r'[\u4e00-\u9fff]{2,6}', text or '')[:n])


def main():
    db = sync.connect()
    items = []

    books = [dict(r) for r in db.execute('SELECT * FROM books WHERE is_mock=0')]
    for b in books:
        bid = b['book_id']
        facts = [dict(r) for r in db.execute('SELECT * FROM facts WHERE book_id=? AND status="reviewed"', (bid,))]
        entities = [dict(r) for r in db.execute('SELECT * FROM entities WHERE book_id=?', (bid,))]
        events = [dict(r) for r in db.execute('SELECT * FROM events WHERE book_id=? ORDER BY order_idx', (bid,))]
        reviewed_frames = [dict(r) for r in db.execute(
            "SELECT * FROM frames WHERE book_id=? AND status='reviewed'", (bid,))]

        # 1) 原书事实题（每fact一题，取claim首关键词做问题主干）
        for f in facts[:14]:
            kws = keywords(f['claim'], 2)
            if not kws:
                continue
            q = f"书里关于{kws[0]}说了什么？"
            items.append(dict(
                qid=f"{bid}_fact_{f['fact_id']}", book_id=bid, qtype='book_fact', question=q,
                expect_keywords=kws, expect_status='answered',
                allowed_evidence=[f['fact_id']], method_hint='extractive_fact',
                verified=None,
            ))

        # 2) 实体外观题（判定口径：回答指向正确实体）
        for e in entities[:4]:
            if not e.get('description'):
                continue
            items.append(dict(
                qid=f"{bid}_entity_{e['entity_id']}", book_id=bid, qtype='entity',
                entity_name=e['name'],
                question=f"{e['name']}长什么样子？",
                expect_keywords=keywords(e['description'], 2)[:2] or [e['name']],
                expect_status='answered',
                allowed_evidence=[], method_hint='any',
                verified=None,
            ))

        # 3) 书名题
        items.append(dict(
            qid=f"{bid}_title", book_id=bid, qtype='book_title',
            question='这本书的名字是什么？',
            expect_keywords=[b['title']], expect_status='answered',
            allowed_evidence=[], method_hint='book_title', verified=None,
        ))

        # 4) 时间顺序题
        for ev in events[:3]:
            kws = keywords(ev['summary'], 2)
            if not kws:
                continue
            items.append(dict(
                qid=f"{bid}_event_{ev['event_id']}", book_id=bid, qtype='timeline',
                question=f"故事里后来发生了{kws[0]}吗？",
                expect_keywords=kws, expect_status='answered',
                allowed_evidence=[ev['event_id']], method_hint='event_order', verified=None,
            ))

        # 5) 视频画面题（reviewed帧 → 自然描述问句，走最近帧claims；判定放宽到clip级）
        video_qs = ['画面里有什么？', '视频里刚才发生了什么？', '画面里有什么东西？']
        vi = 0
        for fr in reviewed_frames[:6]:
            claims = json.loads(fr['claims'] or '[]')
            if not claims:
                continue
            claim = claims[0].get('claim', '')
            kws = keywords(claim, 2)
            if not kws:
                continue
            items.append(dict(
                qid=f"{bid}_frame_{fr['frame_id'][-10:]}", book_id=bid, qtype='video',
                question=video_qs[vi % len(video_qs)], vi=(vi := vi + 1),
                expect_keywords=kws, expect_status='answered',
                allowed_evidence=[fr['frame_id']], method_hint='extractive_frame',
                snapshot=dict(book_id=bid, clip_id=fr['clip_id'],
                              asset_version=fr['asset_version'], media_time_ms=fr['t_ms']),
                verified=None,
            ))

        # 6) 不可回答题（每书3条）
        for i, q in enumerate(['为什么天空是蓝色的？', '这本书有多少个字？', '猫可以活多少年？'][:3]):
            items.append(dict(
                qid=f"{bid}_unans_{i}", book_id=bid, qtype='unanswerable', question=q,
                expect_status='uncertain', expect_keywords=[], allowed_evidence=[],
                verified=None,
            ))

        # 7) 点选指代题（基于reviewed帧claims bbox中心模拟touch，问"它是谁"）
        for fr in reviewed_frames[:3]:
            claims = json.loads(fr['claims'] or '[]')
            for cl in claims[:1]:
                region = cl.get('region') or {}
                if not region:
                    continue
                cx = (region['x1'] + region['x2']) / 2
                cy = (region['y1'] + region['y2']) / 2
                items.append(dict(
                    qid=f"{bid}_pointer_{fr['frame_id'][-10:]}", book_id=bid, qtype='pointer',
                    question='它是谁？',
                    touch=dict(clip_id=fr['clip_id'], asset_version=fr['asset_version'],
                               t_ms=fr['t_ms'], x=cx, y=cy),
                    expect_status='answered', expect_keywords=[], allowed_evidence=[],
                    verified=None,
                ))

    random.Random(42).shuffle(items)
    sealed = items[:40]
    dev = items[40:120]
    out_dir = ROOT / 'evaluation' / 'interaction'
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (('qa_dataset_dev.json', dev), ('qa_dataset_sealed.json', sealed)):
        (out_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf8')
    stats = {}
    for it in items:
        stats[it['qtype']] = stats.get(it['qtype'], 0) + 1
    print(f'题集完成：总计{len(items)}题（开发{len(dev)}/封存{len(sealed)}）')
    print('题型分布:', json.dumps(stats, ensure_ascii=False))


if __name__ == '__main__':
    main()
