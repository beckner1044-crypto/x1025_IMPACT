"""
feedback.py
Crew feedback storage. Each entry foreign-keys to an audit row.

Why SQLite (not JSONL) for feedback specifically:
  - Feedback is queried more than written: "show me thumbs-down answers
    from this month sorted by route." JSONL would mean re-parsing the whole
    file on every review; SQLite gives indexed access.
  - Feedback is intrinsically relational — it points at an audit row by ID.

Why not Gradio's built-in flagging:
  - Gradio's flagging callback is tightly coupled to gr.ChatInterface,
    which we're not using (our UI has the route + evidence pane that the
    chat interface doesn't accommodate cleanly).
  - We want feedback to foreign-key into the audit log, which the built-in
    flagging callback doesn't support out of the box.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import List, Optional


_LOCK = threading.Lock()


class FeedbackStore:
    """Tiny SQLite-backed feedback table."""

    def __init__(self, db_path: str = "./logs/feedback.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        with _LOCK:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts            TEXT NOT NULL,
                    audit_id      TEXT NOT NULL,
                    thumb         TEXT NOT NULL CHECK(thumb IN ('up','down')),
                    note          TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_thumb ON feedback(thumb);
                CREATE INDEX IF NOT EXISTS idx_feedback_audit ON feedback(audit_id);
                CREATE INDEX IF NOT EXISTS idx_feedback_ts    ON feedback(ts);
                """
            )

    # ------------------------------------------------------------------ #
    def add(self, audit_id: str, thumb: str, note: Optional[str] = None) -> None:
        if thumb not in ("up", "down"):
            raise ValueError(f"thumb must be 'up' or 'down', got {thumb!r}")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO feedback (ts, audit_id, thumb, note) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), audit_id, thumb, note),
            )

    def list_thumbs_down(self, limit: int = 50) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE thumb='down' "
                "ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self._conn() as conn:
            up   = conn.execute("SELECT COUNT(*) FROM feedback WHERE thumb='up'").fetchone()[0]
            down = conn.execute("SELECT COUNT(*) FROM feedback WHERE thumb='down'").fetchone()[0]
            return {"up": up, "down": down, "total": up + down}
