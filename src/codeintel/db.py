"""SQLite persistence. One short-lived connection per call (thread-safe for FastAPI workers)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import get_settings
from .models import AnalysisReport, RepoInfo, RepoStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories(
  id TEXT PRIMARY KEY, name TEXT, source TEXT, status TEXT, error TEXT DEFAULT '',
  file_count INTEGER DEFAULT 0, languages TEXT DEFAULT '{}', created_at TEXT);
CREATE TABLE IF NOT EXISTS analyses(
  id TEXT PRIMARY KEY, repo_id TEXT, task TEXT, status TEXT, error TEXT DEFAULT '',
  report TEXT, created_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS idx_analyses_repo ON analyses(repo_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    s = get_settings()
    s.ensure_dirs()
    conn = sqlite3.connect(s.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _repo(row: sqlite3.Row) -> RepoInfo:
    return RepoInfo(
        id=row["id"], name=row["name"], source=row["source"], status=RepoStatus(row["status"]),
        error=row["error"], file_count=row["file_count"], languages=json.loads(row["languages"]),
        created_at=row["created_at"],
    )


def create_repo(name: str, source: str) -> RepoInfo:
    rid = new_id()
    with connect() as c:
        c.execute("INSERT INTO repositories(id,name,source,status,created_at) VALUES(?,?,?,?,?)",
                  (rid, name, source, RepoStatus.pending.value, _now()))
    return get_repo(rid)  # type: ignore[return-value]


def get_repo(rid: str) -> RepoInfo | None:
    with connect() as c:
        row = c.execute("SELECT * FROM repositories WHERE id=?", (rid,)).fetchone()
    return _repo(row) if row else None


def list_repos() -> list[RepoInfo]:
    with connect() as c:
        rows = c.execute("SELECT * FROM repositories ORDER BY created_at DESC").fetchall()
    return [_repo(r) for r in rows]


def update_repo(rid: str, **fields) -> None:
    if "languages" in fields:
        fields["languages"] = json.dumps(fields["languages"])
    if "status" in fields and isinstance(fields["status"], RepoStatus):
        fields["status"] = fields["status"].value
    cols = ",".join(f"{k}=?" for k in fields)   # keys are code-controlled, never user input
    with connect() as c:
        c.execute(f"UPDATE repositories SET {cols} WHERE id=?", (*fields.values(), rid))


def create_analysis(repo_id: str, task: str) -> str:
    aid = new_id()
    with connect() as c:
        c.execute("INSERT INTO analyses(id,repo_id,task,status,created_at) VALUES(?,?,?,?,?)",
                  (aid, repo_id, task, "running", _now()))
    return aid


def finish_analysis(aid: str, report: AnalysisReport | None, error: str = "") -> None:
    with connect() as c:
        c.execute("UPDATE analyses SET status=?, error=?, report=?, finished_at=? WHERE id=?",
                  ("failed" if error else "done", error, report.model_dump_json() if report else None, _now(), aid))


def get_analysis(aid: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM analyses WHERE id=?", (aid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["report"] = json.loads(d["report"]) if d["report"] else None
    return d


def latest_analysis(repo_id: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT id FROM analyses WHERE repo_id=? AND status='done' ORDER BY created_at DESC LIMIT 1",
                        (repo_id,)).fetchone()
    return get_analysis(row["id"]) if row else None


def delete_repo(rid: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM analyses WHERE repo_id=?", (rid,))
        c.execute("DELETE FROM repositories WHERE id=?", (rid,))
