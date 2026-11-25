"""Durable shared state: tasks, messages, blackboard, private memory, events, usage.

Everything lives in one SQLite file, so a crashed or stopped run resumes where it left off.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, title TEXT, detail TEXT, skills TEXT DEFAULT '[]',
    verify_command TEXT, status TEXT DEFAULT 'open', owner TEXT, result TEXT,
    created_by TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, sender TEXT, recipient TEXT, kind TEXT, content TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS reads (agent TEXT PRIMARY KEY, last_message_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS blackboard (
    id INTEGER PRIMARY KEY, author TEXT, kind TEXT, content TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS memory (agent TEXT PRIMARY KEY, notes TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, agent TEXT, session INTEGER, kind TEXT, data TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS usage (
    agent TEXT PRIMARY KEY, input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _x(self, sql: str, args=()) -> int:
        with self._lock:
            return self._db.execute(sql, args).lastrowid

    # meta
    def get_meta(self, key: str, default=None):
        rows = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_meta(self, key: str, value) -> None:
        self._x("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    # tasks
    def create_task(self, title, detail, created_by, skills=None, verify_command=None) -> int:
        return self._x(
            "INSERT INTO tasks (title, detail, skills, verify_command, created_by, updated)"
            " VALUES (?,?,?,?,?,?)",
            (title, detail, json.dumps(skills or []), verify_command, created_by, time.time()),
        )

    def tasks(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._q("SELECT * FROM tasks WHERE status=? ORDER BY id", (status,))
        else:
            rows = self._q("SELECT * FROM tasks ORDER BY id")
        return [dict(r) | {"skills": json.loads(r["skills"])} for r in rows]

    def task(self, task_id: int) -> dict | None:
        rows = self._q("SELECT * FROM tasks WHERE id=?", (task_id,))
        return dict(rows[0]) | {"skills": json.loads(rows[0]["skills"])} if rows else None

    def claim_task(self, task_id: int, agent: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status='claimed', owner=?, updated=? WHERE id=? AND status='open'",
                (agent, time.time(), task_id),
            )
            return cur.rowcount == 1

    def update_task(self, task_id: int, **fields) -> None:
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))

    # messages
    def send(self, sender, recipient, kind, content) -> int:
        return self._x(
            "INSERT INTO messages (sender, recipient, kind, content, ts) VALUES (?,?,?,?,?)",
            (sender, recipient, kind, content, time.time()),
        )

    def _inbox_query(self, agent: str, after: int) -> list[dict]:
        return [dict(r) for r in self._q(
            "SELECT * FROM messages WHERE id>? AND sender!=? AND (recipient=? OR recipient='all')"
            " ORDER BY id", (after, agent, agent))]

    def _last_read(self, agent: str) -> int:
        rows = self._q("SELECT last_message_id FROM reads WHERE agent=?", (agent,))
        return rows[0][0] if rows else 0

    def unread_count(self, agent: str) -> int:
        return len(self._inbox_query(agent, self._last_read(agent)))

    def read_inbox(self, agent: str) -> list[dict]:
        with self._lock:
            msgs = self._inbox_query(agent, self._last_read(agent))
            if msgs:
                self._x("INSERT OR REPLACE INTO reads VALUES (?,?)", (agent, msgs[-1]["id"]))
            return msgs

    # blackboard
    def post(self, author, kind, content) -> int:
        return self._x(
            "INSERT INTO blackboard (author, kind, content, ts) VALUES (?,?,?,?)",
            (author, kind, content, time.time()),
        )

    def board(self, limit: int = 50) -> list[dict]:
        rows = self._q("SELECT * FROM blackboard ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in reversed(rows)]

    # private memory
    def memory(self, agent: str) -> str:
        rows = self._q("SELECT notes FROM memory WHERE agent=?", (agent,))
        return rows[0]["notes"] if rows else ""

    def set_memory(self, agent: str, notes: str) -> None:
        self._x("INSERT OR REPLACE INTO memory VALUES (?,?)", (agent, notes))

    # events (the audit log, and later the training data)
    def log(self, agent, session, kind, data) -> None:
        self._x(
            "INSERT INTO events (agent, session, kind, data, ts) VALUES (?,?,?,?,?)",
            (agent, session, kind, json.dumps(data, default=str), time.time()),
        )

    def events(self) -> list[dict]:
        return [dict(r) | {"data": json.loads(r["data"])}
                for r in self._q("SELECT * FROM events ORDER BY id")]

    # usage
    def add_usage(self, agent, input_tokens, output_tokens, cost) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO usage (agent) VALUES (?)", (agent,))
            self._db.execute(
                "UPDATE usage SET input_tokens=input_tokens+?, output_tokens=output_tokens+?,"
                " cost_usd=cost_usd+? WHERE agent=?",
                (input_tokens, output_tokens, cost, agent),
            )

    def total_cost(self) -> float:
        return self._q("SELECT COALESCE(SUM(cost_usd),0) FROM usage")[0][0]

    def usage(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM usage ORDER BY agent")]
