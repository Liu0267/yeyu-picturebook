"""问答引擎（P0）：意图路由 → 题型分类 → BM25检索 → 摘取式短答 + 可验证证据。

原则：
- 原书事实与实际视频画面分开标注（source:'book' / 'frame'），不互相覆盖。
- 无足够证据时明确返回 uncertain，不强行拼答案。
- 回答短（≤40字），符合儿童伴读场景；延迟优先，全部本地查询（<50ms）。
"""

import json
import re

from . import indexer

UNCERTAINTY = '这段内容里没有足够的依据。可以换个问法，或回到前一页看看。'

# 确定性意图（零延迟，返回command由前端执行媒体控制）
COMMAND_PATTERNS = [
    (r'^(暂停|暂停故事|停一下)$', 'pause'),
    (r'^(继续|继续阅读|接着读|继续故事)$', 'resume'),
    (r'^(上一页|前面一页|翻回去)$', 'page_prev'),
    (r'^(下一页|后面一页|翻过去)$', 'page_next'),
    (r'^(重播|重新播放|再放一遍|从头播放)$', 'replay'),
    (r'^(停止|别说了|不要说了|安静)$', 'cancel'),
]

# 不可回答题型（观点/计算/超范围），扩展自B端手册口径
UNSUPPORTED_RE = re.compile(r'为什么|怎么|如何|多少|几只|几个|什么颜色|谁画|作者|出版社|名字是什么|好不好|该不该|是不是')
VIDEO_RE = re.compile(r'视频|画面|刚才|后来|现在|看到|动画|出现|有没有播放')
POINTER_RE = re.compile(r'^(它|他|她|这个|那个|它们)')
TIMELINE_RE = re.compile(r'先|然后|接着|最后|顺序|后来')


def classify_intent(text):
    """返回 ('command', cmd) 或 ('question', None)。"""
    t = re.sub(r'[。！!？?\s，,]', '', text or '')
    for pat, cmd in COMMAND_PATTERNS:
        if re.fullmatch(pat, t):
            return 'command', cmd
    return 'question', None


def classify_question(text, has_entity):
    """题型：unsupported / pointer / video / timeline / book。"""
    if UNSUPPORTED_RE.search(text) and not re.search(r'书的名字|故事的名字|这本书叫', text):
        return 'unsupported'
    if POINTER_RE.search(text) and has_entity:
        return 'pointer'
    if VIDEO_RE.search(text):
        return 'video'
    if TIMELINE_RE.search(text):
        return 'timeline'
    return 'book'


def _fact_answer(db, q, book_id, page_id=None):
    rows = indexer.search_facts(db, q, book_id, limit=4)
    if page_id:  # 当前页优先（近因boost）
        rows.sort(key=lambda r: (r['page_id'] != page_id, r['rank']))
    if not rows:
        return None
    top = rows[0]
    page = _printed_page(db, top['book_id'], top['page_id'])
    return dict(
        answer_text=f"书里说：{top['claim']}",
        status='answered',
        evidence=[dict(id=top['fact_id'], source='book', page_id=top['page_id'],
                       printed_page=page, fact_kind=top['kind'])],
        method='extractive_fact',
    )


def _printed_page(db, book_id, page_id):
    row = db.execute('SELECT printed_page,sequence FROM pages WHERE book_id=? AND page_id=?',
                     (book_id, page_id)).fetchone()
    if not row:
        return None
    return row['printed_page'] or f"第{(row['sequence'] or 0) + 1}页"


def _frame_answer(db, q, book_id, clip_id, asset_version, t_ms, window_ms):
    rows = indexer.search_frames(db, q, book_id, clip_id, t_ms, window_ms, limit=4)
    if not rows and clip_id:
        # 放宽时间窗再试
        rows = indexer.search_frames(db, q, book_id, clip_id, None, None, limit=4)
    if not rows and re.search(r'有什么|什么样|什么内容|描述|讲的是|看看|发生|做了什么|在做什么|干什么', q):
        # 描述性视频题（"画面里有什么"）：词面检索不适用，直接给时间上最近的已审帧观察
        rows = nearest_reviewed_frames(db, book_id, clip_id, asset_version, t_ms, limit=1)
    if not rows:
        return None
    top = rows[0]
    claim = (top['claims'] or [{}])[0].get('claim', '')
    if not claim:
        return None
    return dict(
        answer_text=f"画面里，{claim}",
        status='answered',
        evidence=[dict(id=top['frame_id'], source='frame', clip_id=top['clip_id'],
                       version=top['asset_version'], ms=top['t_ms'])],
        method='extractive_frame',
    )


def nearest_reviewed_frames(db, book_id, clip_id, asset_version, t_ms, limit=1):
    """按时间距离取最近已审帧（含claims）。"""
    rows = db.execute(
        "SELECT frame_id, clip_id, asset_version, t_ms, claims FROM frames"
        " WHERE book_id=? AND status='reviewed' AND (clip_id=? OR ? IS NULL)"
        " ORDER BY ABS(t_ms-?) LIMIT ?",
        (book_id, clip_id, clip_id, t_ms or 0, limit)).fetchall()
    out = []
    for r in rows:
        out.append(dict(frame_id=r['frame_id'], clip_id=r['clip_id'],
                        asset_version=r['asset_version'], t_ms=r['t_ms'],
                        image_asset_id=None, status='reviewed',
                        claims=json.loads(r['claims'] or '[]')))
    return out


def _pointer_answer(db, entity, book_id):
    """点选指代：基于会话缓存的最近实体，从其对象描述与原书实体描述作答。"""
    if not entity:
        return None
    name = entity.get('name')
    if name:
        # 找该实体的原书事实
        rows = db.execute(
            "SELECT f.fact_id,f.claim,f.page_id FROM facts f JOIN entities e ON e.book_id=f.book_id"
            " WHERE e.name=? AND f.book_id=? AND f.claim LIKE ? ORDER BY f.page_id LIMIT 1",
            (name, book_id, f'%{name}%')).fetchall()
        if rows:
            top = rows[0]
            page = _printed_page(db, book_id, top['page_id'])
            return dict(
                answer_text=f"你点的是{name}。书里说：{top['claim']}",
                status='answered',
                evidence=[dict(id=top['fact_id'], source='book', page_id=top['page_id'],
                               printed_page=page),
                          dict(id=entity.get('object_id', 'touch'), source='frame',
                               clip_id=entity.get('clip_id'), version=entity.get('asset_version'),
                               ms=entity.get('t_ms', 0))],
                method='pointer_fact',
            )
        desc = entity.get('description') or ''
        if desc:
            return dict(
                answer_text=f"这是{name}。画面里，{desc}",
                status='answered',
                evidence=[dict(id=entity.get('object_id', 'touch'), source='frame',
                               clip_id=entity.get('clip_id'), version=entity.get('asset_version'),
                               ms=entity.get('t_ms', 0))],
                method='pointer_object',
            )
    return None


def answer(db, text, book_id, snapshot, entity):
    """主入口。返回 dict(kind='command'|'answer', ...)。"""
    intent, cmd = classify_intent(text)
    if intent == 'command':
        return dict(kind='command', command=cmd,
                    answer_text='', status='command', evidence=[])

    q_type = classify_question(text, has_entity=bool(entity))
    t_ms = snapshot.get('media_time_ms', 0)
    clip_id = snapshot.get('clip_id')
    version = snapshot.get('asset_version')
    page_id = clip_page_id(db, book_id, clip_id, version)

    if q_type == 'unsupported':
        return dict(kind='answer', answer_text=UNCERTAINTY, status='uncertain',
                    evidence=[], method='rule_unsupported')
    if q_type == 'pointer':
        result = _pointer_answer(db, entity, book_id)
        if not result:
            # 指代词但缺少点选上下文：明确澄清，不做垃圾检索兜底
            hint = '你先点一下画面里的角色或物品，再问"它"好吗？'
            return dict(kind='answer', answer_text=hint if entity is None else UNCERTAINTY,
                        status='uncertain', evidence=[], method='pointer_no_context')
        result['kind'] = 'answer'
        return result
    if q_type == 'video':
        result = _frame_answer(db, text, book_id, clip_id, version, t_ms,
                               window_ms=3000)
        if not result:
            # 视频画面没证据时，回退到当前页的原书事实并注明来源不同
            result = _fact_answer(db, text, book_id, page_id)
            if result:
                result['method'] = 'fallback_fact'
        if not result:
            return dict(kind='answer', answer_text=UNCERTAINTY, status='uncertain',
                        evidence=[], method='frame_miss')
        result['kind'] = 'answer'
        return result
    if q_type == 'timeline':
        rows = indexer.search_events(db, text, book_id, limit=3)
        if rows:
            parts = '，'.join(f"{_printed_page(db, book_id, r['page_id'])}:{r['summary']}" for r in rows[:2])
            return dict(kind='answer', answer_text=f"故事顺序是：{parts}",
                        status='answered',
                        evidence=[dict(id=r['event_id'], source='book', page_id=r['page_id'],
                                       printed_page=_printed_page(db, book_id, r['page_id']))
                                  for r in rows[:2]],
                        method='event_order')
        result = _fact_answer(db, text, book_id, page_id)
        if not result:
            return dict(kind='answer', answer_text=UNCERTAINTY, status='uncertain',
                        evidence=[], method='event_miss')
        result['kind'] = 'answer'
        return result
    # book 默认
    # 书名题：直接命中书目表
    if re.search(r'书的名字|故事的名字|这本书叫|书叫什么', text):
        row = db.execute('SELECT title FROM books WHERE book_id=?', (book_id,)).fetchone()
        if row:
            return dict(kind='answer', answer_text=f"这本书是《{row['title']}》。",
                        status='answered',
                        evidence=[dict(id=book_id, source='book', page_id=None)],
                        method='book_title')
    result = _fact_answer(db, text, book_id, page_id)
    if not result:
        # facts无命中 → OCR行检索（原书文字）：低置信行与指代问句不兜底，避免垃圾命中
        rows = [r for r in indexer.search_ocr(db, text, book_id, limit=3)
                if (r.get('confidence') or 0) >= 0.65]
        if rows and not POINTER_RE.search(text):
            top = rows[0]
            page = _printed_page(db, book_id, top['page_id'])
            return dict(kind='answer', answer_text=f"书里写着：{top['text']}",
                        status='answered',
                        evidence=[dict(id=top['line_id'], source='book', page_id=top['page_id'],
                                       printed_page=page)],
                        method='extractive_ocr')
        return dict(kind='answer', answer_text=UNCERTAINTY, status='uncertain',
                    evidence=[], method='book_miss')
    result['kind'] = 'answer'
    return result


def clip_page_id(db, book_id, clip_id, asset_version):
    if not clip_id:
        return None
    row = db.execute('SELECT page_id FROM clips WHERE clip_id=? AND asset_version=?',
                     (clip_id, asset_version)).fetchone()
    return row['page_id'] if row else None
