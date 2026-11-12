"""阅读会话存储（内存版，30分钟TTL）。

每个会话维护：递增seq（防乱序）、最近点选实体、历史轮次（多模态引用）。
多模态历史按匿名会话隔离；音频/图像引用默认只存ID引用，不落真实儿童数据。
"""

import time
import uuid

TTL_MS = 30 * 60 * 1000
MAX_HISTORY = 30


class Session:
    def __init__(self, session_id):
        self.id = session_id
        self.seq = 0
        self.created_ms = time.time() * 1000
        self.expires_ms = self.created_ms + TTL_MS
        self.history = []          # 回答对象列表（含question/evidence/elapsed）
        self.entity = None         # 最近点选实体 {name, object_id, clip_id, asset_version, t_ms, bbox}
        self.last_snapshot = None

    def touch(self):
        self.expires_ms = time.time() * 1000 + TTL_MS

    def push_history(self, record):
        self.history.append(record)
        if len(self.history) > MAX_HISTORY:
            self.history.pop(0)


class SessionStore:
    def __init__(self, max_sessions=500):
        self.sessions = {}
        self.max_sessions = max_sessions

    def create(self):
        if len(self.sessions) >= self.max_sessions:
            self._gc(force=True)
            if len(self.sessions) >= self.max_sessions:
                raise OverflowError('会话已满，请稍后再试。')
        s = Session('sess_' + uuid.uuid4().hex)
        self.sessions[s.id] = s
        return s

    def get(self, session_id):
        self._gc()
        s = self.sessions.get(session_id)
        if s and s.expires_ms > time.time() * 1000:
            return s
        return None

    def _gc(self, force=False):
        now = time.time() * 1000
        expired = [k for k, s in self.sessions.items() if s.expires_ms <= now]
        for k in expired:
            del self.sessions[k]
        if force and len(self.sessions) >= self.max_sessions:
            oldest = sorted(self.sessions.items(), key=lambda kv: kv[1].expires_ms)[:len(self.sessions) // 2]
            for k, _ in oldest:
                del self.sessions[k]
