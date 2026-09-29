"""Append-only audit trail (SQLite). Every decision, model call and side effect is recorded."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any


class AuditLog:
    def __init__(self, path: str = ":memory:"):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, case_id TEXT,"
            " stage TEXT, actor TEXT, payload TEXT)"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS audit_case ON audit(case_id)")
        self.db.commit()

    def record(self, case_id: str, stage: str, actor: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.db.execute("INSERT INTO audit(ts, case_id, stage, actor, payload) VALUES (?,?,?,?,?)",
                            (time.time(), case_id, stage, actor, json.dumps(payload, default=str)))
            self.db.commit()

    def trail(self, case_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT ts, stage, actor, payload FROM audit WHERE case_id=? ORDER BY id",
                                   (case_id,)).fetchall()
        return [{"ts": r[0], "stage": r[1], "actor": r[2], "payload": json.loads(r[3])} for r in rows]
