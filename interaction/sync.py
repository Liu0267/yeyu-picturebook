"""A端数据同步与本地检索索引。

从A端服务拉取 books/manifest/clips/frames，写入 interaction/state/interaction.sqlite，
并为问答/点选建立 FTS5（中文2-gram）倒排索引。后台线程定期轮询实现版本失效。
"""

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import config
from .aclient import AClient

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS books(
  book_id TEXT PRIMARY KEY, title TEXT, is_mock INTEGER, page_count INTEGER,
  revision INTEGER, rights_status TEXT, synced_at TEXT);
CREATE TABLE IF NOT EXISTS pages(
  page_id TEXT PRIMARY KEY, book_id TEXT, sequence INTEGER, printed_page TEXT,
  page_revision INTEGER, image_asset_id TEXT, width INTEGER, height INTEGER,
  ocr_text TEXT, review_status TEXT);
CREATE TABLE IF NOT EXISTS facts(
  fact_id TEXT PRIMARY KEY, book_id TEXT, page_id TEXT, claim TEXT, kind TEXT,
  status TEXT, source_ref_ids TEXT);
CREATE TABLE IF NOT EXISTS entities(
  entity_id TEXT PRIMARY KEY, book_id TEXT, name TEXT, description TEXT, source_ref_ids TEXT);
CREATE TABLE IF NOT EXISTS events(
  event_id TEXT PRIMARY KEY, book_id TEXT, page_id TEXT, summary TEXT,
  order_idx INTEGER, entity_ids TEXT);
CREATE TABLE IF NOT EXISTS clips(
  clip_id TEXT, asset_version INTEGER, book_id TEXT, page_id TEXT, duration_ms INTEGER,
  status TEXT, is_mock INTEGER, asset_id TEXT, audio_asset_id TEXT,
  subtitle_asset_id TEXT, motion_status TEXT, synced_at TEXT,
  PRIMARY KEY(clip_id, asset_version));
CREATE TABLE IF NOT EXISTS frames(
  frame_id TEXT PRIMARY KEY, clip_id TEXT, asset_version INTEGER, book_id TEXT,
  t_ms INTEGER, image_asset_id TEXT, status TEXT, is_mock INTEGER, claims TEXT);
CREATE TABLE IF NOT EXISTS objects(
  object_id TEXT PRIMARY KEY, book_id TEXT, page_id TEXT, clip_id TEXT,
  asset_version INTEGER, t_ms INTEGER, name TEXT, description TEXT,
  bbox TEXT, source TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS draft_objects(
  draft_id TEXT PRIMARY KEY, book_id TEXT, page_id TEXT, page_revision INTEGER,
  name TEXT, description TEXT, bbox TEXT, model_id TEXT);
CREATE TABLE IF NOT EXISTS line_texts(
  line_id TEXT PRIMARY KEY, book_id TEXT, page_id TEXT, text TEXT,
  confidence REAL, region TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  content, fact_id UNINDEXED, book_id UNINDEXED, page_id UNINDEXED, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
  content, line_id UNINDEXED, book_id UNINDEXED, page_id UNINDEXED, confidence UNINDEXED, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS frames_fts USING fts5(
  content, frame_id UNINDEXED, clip_id UNINDEXED, asset_version UNINDEXED,
  t_ms UNINDEXED, book_id UNINDEXED, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
  content, event_id UNINDEXED, book_id UNINDEXED, page_id UNINDEXED, tokenize='unicode61');
CREATE INDEX IF NOT EXISTS idx_frames_clip ON frames(clip_id, asset_version);
CREATE INDEX IF NOT EXISTS idx_objects_clip ON objects(clip_id, asset_version, t_ms);
"""


def bigrams(text):
    """中文2-gram：FTS5 unicode61 对连续中文整串匹配失效，写入与查询统一转为2-gram序列。"""
    t = re.sub(r'\s+', '', (text or '').lower())
    return ' '.join(t[i:i + 2] for i in range(max(0, len(t) - 1))) if len(t) > 1 else t


def connect(db_path=None):
    db_path = Path(db_path or (config.STATE_DIR / 'interaction.sqlite'))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.isolation_level = None  # 手动事务控制
    db.executescript(SCHEMA)
    return db


def _normalize_bbox(bbox, width, height):
    """bbox支持像素或归一化两种输入，统一输出0-1归一化[x1,y1,x2,y2]。"""
    if not bbox or len(bbox) != 4:
        return None
    vals = [float(v) for v in bbox]
    if any(v > 1.5 for v in vals):  # 像素坐标
        if not width or not height:
            return None
        vals = [vals[0] / width, vals[1] / height, vals[2] / width, vals[3] / height]
    x1, y1, x2, y2 = vals
    return [max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1)),
            max(0.0, min(1.0, x2)), max(0.0, min(1.0, y2))]


def _guess_object_name(claim_text, entities):
    """从claim文本匹配实体名作为对象名：先全名匹配，再尾部片段松弛匹配（如"穿靴子的猫"→"猫"）；
    无匹配返回None（点选时显示描述）。"""
    text = claim_text or ''
    best = None
    for name in entities:
        if not name:
            continue
        if name in text:
            if not best or len(name) > len(best):
                best = name
            continue
        # 尾部片段：从1字到完整名，取claim中出现的最长后缀片段
        for k in range(1, len(name)):
            frag = name[-k:]
            if frag in text and (not best or len(frag) > len(best)):
                best = frag
    return best


def rebuild(db, a: AClient):
    """全量重建索引。当前数据量（115页）全量重建约数秒；增量失效由轮询触发重建。"""
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    db.execute('BEGIN')
    for table in ('books', 'pages', 'facts', 'entities', 'events', 'clips', 'frames',
                  'objects', 'line_texts', 'facts_fts', 'ocr_fts', 'frames_fts', 'events_fts'):
        db.execute(f'DELETE FROM {table}')

    stats = dict(books=0, pages=0, facts=0, entities=0, events=0, clips=0, frames=0, objects=0)

    for b in a.get('/v1/books') or []:
        book_id = b['book_id']
        db.execute('INSERT INTO books(book_id,title,is_mock,page_count,revision,rights_status,synced_at)'
                   ' VALUES(?,?,?,?,?,?,?)',
                   (book_id, b['title'], int(bool(b.get('is_mock'))), b['page_count'],
                    b['revision'], b.get('rights_status', ''), now))
        stats['books'] += 1
        manifest = a.get(f'/v1/books/{book_id}/manifest')

        for p in manifest.get('pages', []):
            ocr_text = '\n'.join(ln.get('raw_text', '') for ln in (p.get('ocr_lines') or [])
                                 if ln.get('raw_text'))
            db.execute('INSERT INTO pages(page_id,book_id,sequence,printed_page,page_revision,'
                       'image_asset_id,width,height,ocr_text,review_status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (p['page_id'], book_id, p.get('sequence'), p.get('printed_page'),
                        p.get('page_revision'), p.get('image_asset_id'), p.get('width'),
                        p.get('height'), ocr_text, p.get('review_status', '')))
            stats['pages'] += 1
            for ln in p.get('ocr_lines') or []:
                text = ln.get('raw_text', '')
                if text.strip():
                    db.execute('INSERT INTO line_texts(line_id,book_id,page_id,text,confidence,region)'
                               ' VALUES(?,?,?,?,?,?)',
                               (ln.get('line_id'), book_id, p['page_id'], text,
                                ln.get('confidence', 0.0),
                                json.dumps(ln.get('region'), ensure_ascii=False)))
                    db.execute('INSERT INTO ocr_fts(content,line_id,book_id,page_id,confidence)'
                               ' VALUES(?,?,?,?,?)',
                               (bigrams(text), ln.get('line_id'), book_id, p['page_id'],
                                ln.get('confidence', 0.0)))

        for f in manifest.get('facts', []) or []:
            db.execute('INSERT INTO facts(fact_id,book_id,page_id,claim,kind,status,source_ref_ids)'
                       ' VALUES(?,?,?,?,?,?,?)',
                       (f['fact_id'], book_id, f.get('page_id'), f.get('claim', ''),
                        f.get('kind', ''), f.get('status', ''),
                        json.dumps(f.get('source_ref_ids', []), ensure_ascii=False)))
            db.execute('INSERT INTO facts_fts(content,fact_id,book_id,page_id) VALUES(?,?,?,?)',
                       (bigrams(f.get('claim', '')), f['fact_id'], book_id, f.get('page_id')))
            stats['facts'] += 1

        entity_names = []
        for e in manifest.get('entities', []) or []:
            db.execute('INSERT INTO entities(entity_id,book_id,name,description,source_ref_ids)'
                       ' VALUES(?,?,?,?,?)',
                       (e['entity_id'], book_id, e.get('name', ''), e.get('description', ''),
                        json.dumps(e.get('source_ref_ids', []), ensure_ascii=False)))
            entity_names.append(e.get('name', ''))
            stats['entities'] += 1

        for ev in manifest.get('events', []) or []:
            db.execute('INSERT INTO events(event_id,book_id,page_id,summary,order_idx,entity_ids)'
                       ' VALUES(?,?,?,?,?,?)',
                       (ev['event_id'], book_id, ev.get('page_id'), ev.get('summary', ''),
                        ev.get('order', ev.get('order_idx', 0)),
                        json.dumps(ev.get('entity_ids', []), ensure_ascii=False)))
            db.execute('INSERT INTO events_fts(content,event_id,book_id,page_id) VALUES(?,?,?,?)',
                       (bigrams(ev.get('summary', '')), ev['event_id'], book_id, ev.get('page_id')))
            stats['events'] += 1

        # clips + frames（含待审核；可见性由查询层按 status/is_mock 过滤）
        data = a.get(f'/v1/books/{book_id}/clips?include_pending=true') or {}
        clip_pages = {}
        for c in data.get('clips', []):
            clip_pages[c['clip_id']] = c.get('page_id')
            db.execute('INSERT OR REPLACE INTO clips(clip_id,asset_version,book_id,page_id,duration_ms,'
                       'status,is_mock,asset_id,audio_asset_id,subtitle_asset_id,motion_status,synced_at)'
                       ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (c['clip_id'], c['asset_version'], book_id, c.get('page_id'),
                        c.get('duration_ms', 0), c['status'], int(bool(c.get('is_mock'))),
                        c.get('asset_id'), c.get('audio_asset_id'), c.get('subtitle_asset_id'),
                        c.get('motion_status', ''), now))
            stats['clips'] += 1

        for fr in data.get('frame_evidence', []):
            db.execute('INSERT OR REPLACE INTO frames(frame_id,clip_id,asset_version,book_id,t_ms,'
                       'image_asset_id,status,is_mock,claims) VALUES(?,?,?,?,?,?,?,?,?)',
                       (fr['frame_id'], fr['clip_id'], fr['asset_version'], book_id,
                        fr.get('t_ms', 0), fr.get('image_asset_id'), fr.get('status', ''),
                        int(bool(fr.get('is_mock'))),
                        json.dumps(fr.get('visual_claims', []), ensure_ascii=False)))
            stats['frames'] += 1
            claim_texts = []
            for i, cl in enumerate(fr.get('visual_claims') or []):
                claim_text = cl.get('claim', '')
                region = cl.get('region') or {}
                bbox = _normalize_bbox(
                    [region.get('x1'), region.get('y1'), region.get('x2'), region.get('y2')], None, None) \
                    if region else None
                db.execute('INSERT INTO objects(object_id,book_id,page_id,clip_id,asset_version,'
                           't_ms,name,description,bbox,source,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (f"{fr['frame_id']}_c{i}", book_id, clip_pages.get(fr['clip_id']),
                            fr['clip_id'], fr['asset_version'], fr.get('t_ms', 0),
                            _guess_object_name(claim_text, entity_names), claim_text,
                            json.dumps(bbox) if bbox else None,
                            'frame_claim', fr.get('status', '')))
                stats['objects'] += 1
                claim_texts.append(claim_text)
            if claim_texts:
                db.execute('INSERT INTO frames_fts(content,frame_id,clip_id,asset_version,t_ms,book_id)'
                           ' VALUES(?,?,?,?,?,?)',
                           (bigrams('；'.join(claim_texts)), fr['frame_id'], fr['clip_id'],
                            fr['asset_version'], fr.get('t_ms', 0), book_id))

    db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_sync',?)", (now,))
    db.execute('COMMIT')
    return stats


def fingerprint(db):
    """书的revision与clips状态指纹，用于轮询检测变更。"""
    parts = []
    for row in db.execute('SELECT book_id,revision FROM books ORDER BY book_id'):
        parts.append(f"{row['book_id']}:{row['revision']}")
    for row in db.execute('SELECT clip_id,asset_version,status FROM clips ORDER BY clip_id, asset_version'):
        parts.append(f"{row['clip_id']}:v{row['asset_version']}:{row['status']}")
    return '|'.join(parts)


def sync_once(db, a: AClient):
    """同步一次；指纹无变化时跳过重建。返回 dict(rebuilt, stats)。"""
    before = fingerprint(db) if db.execute('SELECT COUNT(*) c FROM books').fetchone()['c'] else ''
    after = _probe_fingerprint(db, a)
    if after is None or after == before:
        return dict(rebuilt=False, stats=None)
    stats = rebuild(db, a)
    return dict(rebuilt=True, stats=stats)


def _probe_fingerprint(db, a: AClient):
    """拉取A端轻量状态生成指纹；A端不可达返回None（保持现状）。"""
    try:
        books = a.get('/v1/books') or []
    except Exception:
        return None  # A端不可达：保持现状
    rows = db.execute('SELECT book_id,revision,is_mock,page_count FROM books').fetchall()
    if len(rows) != len(books):
        return 'changed'
    for b in books:
        match = next((r for r in rows if r['book_id'] == b['book_id']), None)
        if not match or match['revision'] != b['revision'] or \
                bool(match['is_mock']) != bool(b.get('is_mock')) or \
                match['page_count'] != b['page_count']:
            return 'changed'
    summary = []
    for r in rows:
        try:
            data = a.get(f"/v1/books/{r['book_id']}/clips?include_pending=true") or {}
        except Exception:
            return None
        for c in data.get('clips', []):
            summary.append(f"{c['clip_id']}:v{c['asset_version']}:{c['status']}")
    return 'books:' + '|'.join(f"{r['book_id']}:{r['revision']}" for r in rows) \
        + '#clips:' + '|'.join(sorted(summary))


def start_background(db, a: AClient, interval=None):
    """后台轮询线程：每 interval 秒探测A端变更并重建索引。返回线程（daemon）。"""
    interval = interval or config.SYNC_INTERVAL_S

    def loop():
        while True:
            time.sleep(interval)
            try:
                result = sync_once(db, a)
                if result['rebuilt']:
                    print(f'[sync] 索引已重建: {result["stats"]}')
            except Exception as exc:  # 轮询失败不影响服务
                print(f'[sync] 轮询失败: {exc}')

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
