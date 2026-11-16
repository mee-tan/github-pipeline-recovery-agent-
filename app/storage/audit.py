"""Audit log: one row per node that ran, for every recovery (FR14, AC8).

Uses SQLite from Python's standard library. ":memory:" keeps it in memory (for tests);
a file path such as "recoveries.db" keeps it on disk.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class AuditRow:
    recovery_id: str
    at: str          # UTC time, ISO format
    node: str
    status: str      # the recovery's status after this node
    detail: str | None


class AuditLog:
    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS audit ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " recovery_id TEXT NOT NULL,"
            " at TEXT NOT NULL,"
            " node TEXT NOT NULL,"
            " status TEXT NOT NULL,"
            " detail TEXT)"
        )
        self._db.commit()

    def record(self, recovery_id: str, node: str, status: str, detail: str | None = None) -> None:
        # "?" placeholders: values are passed separately, never pasted into the SQL text,
        # so nothing in them can change the query (no SQL injection).
        self._db.execute(
            "INSERT INTO audit (recovery_id, at, node, status, detail) VALUES (?, ?, ?, ?, ?)",
            (recovery_id, datetime.now(UTC).isoformat(), node, str(status), detail),
        )
        self._db.commit()

    def rows(self, recovery_id: str) -> list[AuditRow]:
        cursor = self._db.execute(
            "SELECT recovery_id, at, node, status, detail FROM audit"
            " WHERE recovery_id = ? ORDER BY id",
            (recovery_id,),
        )
        return [AuditRow(*row) for row in cursor.fetchall()]
