"""Transactional project facts and replayable records; originals are read-only."""

from contextlib import contextmanager
from pathlib import Path
import json
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..persistence import atomic_write_json, read_json_object


def identifier():
    return uuid.uuid4().hex


def encoded(value, limit=2_000_000):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(text.encode('utf8')) > limit:
        raise ValueError('工程记录超过大小限制。')
    return text


class Project:
    def __init__(self, path, manifest):
        if not isinstance(manifest.get('id'), str) or not re.fullmatch(
            '[0-9a-f]{32}', manifest['id']
        ):
            raise ValueError('工程标识无效。')
        if not isinstance(manifest.get('name'), str) or not 0 < len(manifest['name']) <= 120:
            raise ValueError('工程名称无效。')
        try:
            ZoneInfo(manifest.get('timezone', 'Asia/Hong_Kong'))
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError('工程时区无效。') from None
        self.path = Path(path).resolve()
        self.manifest = manifest
        directory = manifest.get('data_directory', '')
        if (
            not isinstance(directory, str)
            or Path(directory).name != directory
            or directory in ('', '.', '..')
        ):
            raise ValueError('工程数据目录无效。')
        self.root = self.path.parent / directory
        if self.root.is_symlink() or self.root.is_junction():
            raise ValueError('工程数据目录不能是链接。')
        if self.root.resolve().parent != self.path.parent:
            raise ValueError('工程数据目录超出范围。')
        self.root.mkdir(exist_ok=True)
        for name in ('previews', 'derived', 'agent', 'sessions'):
            folder = self.root / name
            if folder.is_symlink() or folder.is_junction():
                raise ValueError('工程输出目录不能是链接。')
            folder.mkdir(exist_ok=True)
        self.database = self.root / 'project.sqlite3'
        if self.database.is_symlink():
            raise ValueError('工程数据库不能是链接。')
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise ValueError('工程版本高于当前软件支持的版本。')
            db.executescript("""
                CREATE TABLE IF NOT EXISTS photos (
                    id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL,
                    size INTEGER, mtime INTEGER, status TEXT DEFAULT 'pending',
                    sha TEXT DEFAULT '', facts TEXT DEFAULT '{}');
                CREATE INDEX IF NOT EXISTS photos_sha ON photos(sha);
                CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, payload TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT, status TEXT, detail TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, photo_id TEXT REFERENCES photos(id), kind TEXT,
                    payload TEXT, status TEXT DEFAULT 'pending', created REAL);
                CREATE TABLE IF NOT EXISTS derivatives (
                    id TEXT PRIMARY KEY, photo_id TEXT REFERENCES photos(id), path TEXT,
                    provenance TEXT, created REAL);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA journal_mode=WAL')
        try:
            with db:
                yield db
        finally:
            db.close()

    @classmethod
    def create(cls, path, name):
        path = Path(path).resolve().with_suffix('.ccrawagent')
        if path.exists() or path.with_suffix('.ccrawagent-data').exists():
            raise ValueError('工程已存在，请打开现有工程或选择新名称。')
        path.parent.mkdir(parents=True, exist_ok=True)
        manifest = dict(
            application='CCRaw Photo Agent',
            version=1,
            id=identifier(),
            name=str(name)[:120],
            data_directory=path.with_suffix('.ccrawagent-data').name,
            timezone='Asia/Hong_Kong',
        )
        project = cls(path, manifest)
        atomic_write_json(path, manifest)
        project.event('project.created', {'name': manifest['name']})
        return project

    @classmethod
    def open(cls, path):
        manifest = read_json_object(path, max_bytes=65536)
        if manifest.get('application') != 'CCRaw Photo Agent' or manifest.get('version') != 1:
            raise ValueError('请选择 CCRaw Photo Agent 工程。')
        return cls(path, manifest)

    def import_paths(self, paths):
        from ..engine import RAW_EXTENSIONS

        supported = RAW_EXTENSIONS | {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp'}
        added = []
        with self.connect() as db:
            total = db.execute('SELECT count(*) FROM photos').fetchone()[0]
            for item in paths:
                path = Path(item)
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in supported:
                    continue
                path = path.resolve()
                stat = path.stat()
                photo_id = identifier()
                cursor = db.execute(
                    'INSERT OR IGNORE INTO photos(id,path,size,mtime) VALUES(?,?,?,?)',
                    (photo_id, str(path), stat.st_size, stat.st_mtime_ns),
                )
                if cursor.rowcount:
                    added.append(photo_id)
                    total += 1
                if total > 100_000:
                    raise ValueError('单个工程最多导入 100000 张照片。')
        self.event('photos.imported', {'added': len(added)})
        return added

    @staticmethod
    def _photo(row):
        result = dict(row)
        if not isinstance(result['id'], str) or not re.fullmatch('[0-9a-f]{32}', result['id']):
            raise ValueError('照片标识无效，工程记录可能已损坏。')
        result['facts'] = json.loads(result['facts'])
        if not isinstance(result['facts'], dict):
            raise ValueError('照片事实记录无效。')
        return result

    def photo(self, photo_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
        if row is None:
            raise ValueError('照片不属于当前工程。')
        return self._photo(row)

    def photos(self, limit=10000, offset=0):
        with self.connect() as db:
            rows = db.execute(
                'SELECT * FROM photos ORDER BY rowid LIMIT ? OFFSET ?',
                (min(10000, max(1, int(limit))), max(0, int(offset))),
            ).fetchall()
        return [self._photo(row) for row in rows]

    def update_photo(self, photo_id, *, facts, sha='', status='ready', size=None, mtime=None):
        self.photo(photo_id)
        with self.connect() as db:
            db.execute(
                'UPDATE photos SET facts=?,sha=?,status=?,size=coalesce(?,size),mtime=coalesce(?,mtime) WHERE id=?',
                (encoded(facts), sha, status, size, mtime, photo_id),
            )

    def summary(self):
        with self.connect() as db:
            counts = dict(db.execute('SELECT status,count(*) FROM photos GROUP BY status'))
            derived = db.execute('SELECT count(*) FROM derivatives').fetchone()[0]
        return dict(
            photos=sum(counts.values()),
            indexed=counts.get('ready', 0),
            pending=counts.get('pending', 0),
            failed=counts.get('failed', 0),
            derivatives=derived,
        )

    def preference(self, key, value):
        with self.connect() as db:
            db.execute(
                'INSERT OR REPLACE INTO preferences VALUES(?,?)',
                (
                    str(key)[:80],
                    encoded(value, 2_000_000 if str(key).startswith('recipe:') else 8192),
                ),
            )

    def preferences(self):
        with self.connect() as db:
            return {
                r['key']: json.loads(r['value']) for r in db.execute('SELECT * FROM preferences')
            }

    def message(self, role, content):
        if (
            role not in ('user', 'assistant', 'system')
            or not isinstance(content, str)
            or len(content) > 16000
        ):
            raise ValueError('会话消息无效或超过 16000 字。')
        with self.connect() as db:
            db.execute(
                'INSERT INTO messages(role,content,created) VALUES(?,?,?)',
                (role, content, time.time()),
            )

    def messages(self, limit=100):
        with self.connect() as db:
            rows = db.execute(
                'SELECT * FROM messages ORDER BY seq DESC LIMIT ?', (min(200, limit),)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def event(self, kind, payload):
        with self.connect() as db:
            db.execute(
                'INSERT INTO events(kind,payload,created) VALUES(?,?,?)',
                (str(kind)[:80], encoded(payload, 64000), time.time()),
            )

    def events(self, limit=200):
        with self.connect() as db:
            rows = db.execute(
                'SELECT * FROM events ORDER BY seq DESC LIMIT ?', (min(1000, limit),)
            ).fetchall()
        return [dict(r, payload=json.loads(r['payload'])) for r in reversed(rows)]

    def start_job(self, kind):
        job_id = identifier()
        with self.connect() as db:
            db.execute(
                'INSERT INTO jobs VALUES(?,?,?,?,?)', (job_id, kind, 'running', '', time.time())
            )
        self.event('job.started', {'id': job_id, 'kind': kind})
        return job_id

    def finish_job(self, job_id, status, detail=''):
        with self.connect() as db:
            db.execute(
                'UPDATE jobs SET status=?,detail=? WHERE id=?', (status, str(detail)[:1000], job_id)
            )
        self.event('job.' + status, {'id': job_id, 'detail': str(detail)[:1000]})

    def recover(self):
        with self.connect() as db:
            count = db.execute(
                "UPDATE jobs SET status='interrupted' WHERE status='running'"
            ).rowcount
            db.execute("UPDATE proposals SET status='interrupted' WHERE status='executing'")
        if count:
            self.event('jobs.interrupted', {'count': count, 'replay': 'records only'})
        return count

    def proposal(self, photo_id, kind, payload):
        self.photo(photo_id)
        proposal_id = identifier()
        with self.connect() as db:
            db.execute(
                'INSERT INTO proposals(id,photo_id,kind,payload,created) VALUES(?,?,?,?,?)',
                (proposal_id, photo_id, kind, encoded(payload), time.time()),
            )
        return proposal_id

    def proposals(self, limit=100):
        with self.connect() as db:
            rows = db.execute(
                'SELECT * FROM proposals ORDER BY created DESC LIMIT ?', (min(100, limit),)
            ).fetchall()
        return [dict(r, payload=json.loads(r['payload'])) for r in rows]

    def claim_proposal(self, proposal_id):
        with self.connect() as db:
            row = db.execute(
                "UPDATE proposals SET status='executing' WHERE id=? AND status='pending' RETURNING *",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise ValueError('方案已执行或已中断，请重新创建方案。')
        return dict(row, payload=json.loads(row['payload']))

    def proposal_status(self, proposal_id, status):
        with self.connect() as db:
            db.execute('UPDATE proposals SET status=? WHERE id=?', (status, proposal_id))

    def output(self, name):
        if not isinstance(name, str) or Path(name).name != name or name in ('', '.', '..'):
            raise ValueError('派生文件名称无效。')
        path = self.directory('derived') / name
        if path.resolve().parent != (self.root / 'derived').resolve() or path.exists():
            raise ValueError('派生文件不能覆盖现有文件或超出工程目录。')
        return path

    def directory(self, name):
        if name not in ('previews', 'derived', 'agent', 'sessions'):
            raise ValueError('工程目录名称无效。')
        folder = self.root / name
        if (
            self.root.is_symlink()
            or self.root.is_junction()
            or folder.is_symlink()
            or folder.is_junction()
            or folder.resolve().parent != self.root
        ):
            raise ValueError('工程目录发生变化或超出范围。')
        return folder

    def derivative(self, photo_id, path, provenance):
        self.photo(photo_id)
        path = Path(path).resolve()
        if path.parent != (self.root / 'derived').resolve() or not path.is_file():
            raise ValueError('派生版本必须保存于当前工程。')
        with self.connect() as db:
            db.execute(
                'INSERT INTO derivatives VALUES(?,?,?,?,?)',
                (identifier(), photo_id, path.name, encoded(provenance), time.time()),
            )

    def derivatives(self):
        with self.connect() as db:
            rows = db.execute(
                'SELECT * FROM derivatives ORDER BY created DESC LIMIT 200'
            ).fetchall()
        return [
            dict(
                r,
                path=str(self.root / 'derived' / r['path']),
                provenance=json.loads(r['provenance']),
            )
            for r in rows
        ]
