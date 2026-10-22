import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


class DB:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as conn:
            conn.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS submissions (
                    chat_id INTEGER NOT NULL, week TEXT NOT NULL,
                    user_id INTEGER NOT NULL, name TEXT NOT NULL, body TEXT NOT NULL,
                    updated_at TEXT NOT NULL, last_update_id INTEGER NOT NULL,
                    PRIMARY KEY(chat_id, week, user_id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL,
                    week TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
                    error_type TEXT
                );
            ''')
            # 단일 인스턴스 전제. 재시작 도중 중단된 작업은 자동 재호출하지 않음
            conn.execute("UPDATE jobs SET state='interrupted' WHERE state='running'")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def submit(self, chat, week, user, name, body, update_id):
        with self.connect() as c:
            c.execute('''INSERT INTO submissions VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(chat_id,week,user_id) DO UPDATE SET
                name=excluded.name, body=excluded.body, updated_at=excluded.updated_at,
                last_update_id=excluded.last_update_id
                WHERE excluded.last_update_id > submissions.last_update_id''',
                (chat, week, user, name, body, datetime.now(timezone.utc).isoformat(), update_id))

    def list(self, chat, week):
        with self.connect() as c:
            return [dict(r) for r in c.execute(
                'SELECT * FROM submissions WHERE chat_id=? AND week=? ORDER BY user_id', (chat, week))]

    def job(self, job_id, chat, week):
        with self.connect() as c:
            return c.execute('INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,NULL)',
                (job_id, chat, week, 'running', datetime.now(timezone.utc).isoformat())).rowcount == 1

    def finish(self, job_id, state, error_type=None):
        with self.connect() as c:
            c.execute('UPDATE jobs SET state=?,error_type=? WHERE job_id=?', (state, error_type, job_id))
