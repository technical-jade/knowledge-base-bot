"""Состояние бота в SQLite (ТЗ, раздел 9).

Сессия чата, текущая статья, статья → сессия Claude, message_id → статья,
журнал задач. Секретов здесь нет — только пути, id и короткие итоги.
"""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS article_sessions (
    path TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    message_id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started REAL NOT NULL,
    finished REAL,
    intent TEXT NOT NULL,
    result TEXT,
    detail TEXT,
    commit_sha TEXT,
    duration REAL
);
"""

# Ротация (этап 7): журнал задач и привязки сообщений не растут бесконечно
KEEP_TASKS = 2000
KEEP_MESSAGES = 5000


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.commit()

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def _exec(self, sql: str, args: tuple = ()) -> list[tuple]:
        with closing(self._db()) as db:
            rows = db.execute(sql, args).fetchall()
            db.commit()
            return rows

    # kv
    def get(self, key: str) -> str | None:
        rows = self._exec("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    def set(self, key: str, value: str | None) -> None:
        if value is None:
            self._exec("DELETE FROM kv WHERE key = ?", (key,))
        else:
            self._exec("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    @property
    def chat_session(self) -> str | None:
        return self.get("chat_session")

    @chat_session.setter
    def chat_session(self, sid: str | None) -> None:
        self.set("chat_session", sid)

    @property
    def current_article(self) -> str | None:
        return self.get("current_article")

    @current_article.setter
    def current_article(self, path: str | None) -> None:
        self.set("current_article", path)

    # статья → сессия Claude
    def article_session(self, path: str) -> str | None:
        rows = self._exec("SELECT session_id FROM article_sessions WHERE path = ?", (path,))
        return rows[0][0] if rows else None

    def bind_session(self, path: str, session_id: str) -> None:
        self._exec(
            "INSERT INTO article_sessions(path, session_id, updated) VALUES(?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET session_id = excluded.session_id, updated = excluded.updated",
            (path, session_id, time.time()),
        )

    def rename_article(self, old: str, new: str) -> None:
        """После переноса/удаления привязки переезжают вместе со статьёй."""
        self._exec("DELETE FROM article_sessions WHERE path = ?", (new,))
        self._exec("UPDATE article_sessions SET path = ? WHERE path = ?", (new, old))
        self._exec("UPDATE messages SET path = ? WHERE path = ?", (new, old))
        if self.current_article == old:
            self.current_article = new

    def forget_article(self, path: str) -> None:
        self._exec("DELETE FROM article_sessions WHERE path = ?", (path,))
        self._exec("DELETE FROM messages WHERE path = ?", (path,))
        if self.current_article == path:
            self.current_article = None

    # message_id → статья
    def bind_message(self, message_id: int, path: str) -> None:
        self._exec("INSERT OR REPLACE INTO messages(message_id, path, created) VALUES(?, ?, ?)", (message_id, path, time.time()))

    def message_article(self, message_id: int) -> str | None:
        rows = self._exec("SELECT path FROM messages WHERE message_id = ?", (message_id,))
        return rows[0][0] if rows else None

    # журнал задач
    def task_start(self, intent: str) -> int:
        with closing(self._db()) as db:
            cur = db.execute("INSERT INTO tasks(started, intent) VALUES(?, ?)", (time.time(), intent))
            db.commit()
            return int(cur.lastrowid)

    def task_finish(self, task_id: int, result: str, detail: str = "", commit_sha: str | None = None) -> None:
        now = time.time()
        self._exec(
            "UPDATE tasks SET finished = ?, result = ?, detail = ?, commit_sha = ?, duration = ? - started WHERE id = ?",
            (now, result, detail[:500], commit_sha, now, task_id),
        )

    def tasks(self, limit: int = 20) -> list[tuple]:
        return self._exec(
            "SELECT id, started, intent, result, detail, commit_sha, duration FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        )

    def prune(self) -> None:
        self._exec("DELETE FROM tasks WHERE id <= (SELECT MAX(id) FROM tasks) - ?", (KEEP_TASKS,))
        self._exec(
            "DELETE FROM messages WHERE message_id NOT IN (SELECT message_id FROM messages ORDER BY created DESC LIMIT ?)",
            (KEEP_MESSAGES,),
        )
