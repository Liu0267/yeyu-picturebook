"""Small local SQLite store. Single worker only; no external job broker needed for M0."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS objects(kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id));
                CREATE TABLE IF NOT EXISTS requests(key TEXT PRIMARY KEY, hash TEXT NOT NULL, run_id TEXT NOT NULL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / 'state.sqlite', timeout=20)
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, kind, key, db=None):
        if db is None:
            with self.connect() as conn:
                return self.get(kind, key, conn)
        row = db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind, key, value, db=None):
        if db is None:
            with self.connect() as conn:
                return self.put(kind, key, value, conn)
        if hasattr(value, 'model_dump'):
            value = value.model_dump(mode='json')
        db.execute('INSERT INTO objects VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body',
                   (kind, key, json.dumps(value, ensure_ascii=False)))

    def all(self, kind, db=None):
        if db is None:
            with self.connect() as conn:
                return self.all(kind, conn)
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM objects WHERE kind=? ORDER BY id', (kind,))]

    def asset(self, path, media_type, is_mock=False):
        path = Path(path).resolve()
        relative = path.relative_to(self.root)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        key = 'asset_' + sha[:24]
        self.put('asset', key, dict(asset_id=key, path=str(relative), sha256=sha,
                                  media_type=media_type, is_mock=is_mock, size=path.stat().st_size))
        return key

    def asset_path(self, key, verify=False, db=None):
        record = self.get('asset', key, db)
        if record is None:
            raise FileNotFoundError(key)
        path = (self.root / record['path']).resolve()
        path.relative_to(self.root)
        if not path.is_file():
            raise FileNotFoundError(key)
        if verify:
            with path.open('rb') as source:actual=hashlib.file_digest(source,'sha256').hexdigest()
            if actual!=record['sha256']:raise ValueError('ASSET_HASH_MISMATCH')
        return path

