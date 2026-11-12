"""本地索引检索接口（FTS5 + BM25，中文2-gram）。

供问答（qa.py）、点选（touch.py）与调试接口 /v1/search 使用。
可见性规则：视频帧/对象只属于 approved 片段（或演示模式下的 approved Mock 片段）。
"""

import json

from . import config
from .sync import bigrams


def _fts_query(text):
    return ' OR '.join(f'"{g}"' for g in bigrams(text).split() if g)


def search_facts(db, q, book_id=None, limit=5):
    sql = ("SELECT f.fact_id, f.book_id, f.page_id, f.claim, f.kind, f.status,"
           " bm25(facts_fts) rank FROM facts_fts JOIN facts f ON f.fact_id=facts_fts.fact_id"
           " WHERE facts_fts MATCH ?")
    args = [_fts_query(q)]
    if book_id:
        sql += ' AND f.book_id=?'
        args.append(book_id)
    sql += ' ORDER BY rank LIMIT ?'
    args.append(limit)
    return [dict(r) for r in db.execute(sql, args)]


def search_ocr(db, q, book_id=None, page_id=None, limit=5):
    sql = ("SELECT o.line_id, o.book_id, o.page_id, p.printed_page, o.confidence,"
           " o.content, bm25(ocr_fts) rank FROM ocr_fts o JOIN pages p ON p.page_id=o.page_id"
           " WHERE ocr_fts MATCH ?")
    args = [_fts_query(q)]
    if book_id:
        sql += ' AND o.book_id=?'
        args.append(book_id)
    if page_id:
        sql += ' AND o.page_id=?'
        args.append(page_id)
    sql += ' ORDER BY rank LIMIT ?'
    args.append(limit)
    rows = []
    for r in db.execute(sql, args):
        d = dict(r)
        # 还原原始文本（content是2-gram序列）：从pages表的ocr_text按行找回
        d['text'] = _original_line(db, d['line_id'])
        rows.append(d)
    return rows


def _original_line(db, line_id):
    """由line_id从A端manifest缓存pages.ocr_text还原原始行文本较难（无行分隔结构），
    因此ocr行原文在同步时冗余存入line_texts表。"""
    row = db.execute('SELECT text FROM line_texts WHERE line_id=?', (line_id,)).fetchone()
    return row['text'] if row else ''


def search_frames(db, q, book_id=None, clip_id=None, t_ms=None, window_ms=None, limit=5):
    """视频画面检索：仅引用 status='reviewed' 的帧（实际画面观察，可验证）。
    片段发布状态由播放器与评测层另行过滤；已审帧的claims属于已核实画面事实。"""
    sql = ("SELECT fr.frame_id, fr.clip_id, fr.asset_version, fr.t_ms, fr.book_id, fr.image_asset_id,"
           " fr.status, bm25(frames_fts) rank FROM frames_fts JOIN frames fr ON fr.frame_id=frames_fts.frame_id"
           " WHERE frames_fts MATCH ? AND fr.status='reviewed'")
    args = [_fts_query(q)]
    if book_id:
        sql += ' AND fr.book_id=?'
        args.append(book_id)
    if clip_id:
        sql += ' AND fr.clip_id=?'
        args.append(clip_id)
    if t_ms is not None and window_ms is not None:
        sql += ' AND fr.t_ms BETWEEN ? AND ?'
        args.extend([max(0, t_ms - window_ms), t_ms + window_ms])
    sql += ' ORDER BY rank LIMIT ?'
    args.append(limit)
    rows = []
    for r in db.execute(sql, args):
        d = dict(r)
        d['claims'] = json.loads(db.execute('SELECT claims FROM frames WHERE frame_id=?',
                                            (d['frame_id'],)).fetchone()['claims'] or '[]')
        rows.append(d)
    return rows


def search_events(db, q, book_id=None, limit=5):
    sql = ("SELECT e.event_id, e.book_id, e.page_id, e.summary, e.order_idx,"
           " bm25(events_fts) rank FROM events_fts JOIN events e ON e.event_id=events_fts.event_id"
           " WHERE events_fts MATCH ?")
    args = [_fts_query(q)]
    if book_id:
        sql += ' AND e.book_id=?'
        args.append(book_id)
    sql += ' ORDER BY e.order_idx, rank LIMIT ?'
    args.append(limit)
    return [dict(r) for r in db.execute(sql, args)]


def objects_for_touch(db, clip_id, asset_version, t_ms, window_ms=None):
    """点选L1：返回该片段、邻近时间窗内、reviewed帧claims解析出的对象候选。"""
    window_ms = window_ms if window_ms is not None else config.TOUCH_FRAME_WINDOW_MS
    sql = ("SELECT o.* FROM objects o WHERE o.clip_id=? AND o.asset_version=? AND o.t_ms BETWEEN ? AND ?"
           " ORDER BY ABS(o.t_ms-?) LIMIT 3")
    lo, hi = max(0, t_ms - window_ms), t_ms + window_ms
    return [dict(r) for r in db.execute(sql, (clip_id, asset_version, lo, hi, t_ms))]


def visible_clips(db, book_id=None, include_pending=False):
    sql = ('SELECT * FROM clips WHERE (status=\'approved\''
           + (' OR is_mock=1' if config.ALLOW_MOCK else '') + ')')
    args = []
    if include_pending:
        sql = 'SELECT * FROM clips WHERE 1'
    if book_id:
        sql += ' AND book_id=?'
        args.append(book_id)
    sql += ' ORDER BY book_id, page_id, asset_version DESC'
    return [dict(r) for r in db.execute(sql, args)]
