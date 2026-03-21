import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class OutboxItem:
    id: int
    event_id: str
    event_payload: str
    image_path: str
    event_published: bool
    image_published: bool
    retry_count: int
    next_retry_ts: float


class PiOutbox:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    created_ts REAL NOT NULL,
                    event_payload TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    event_published INTEGER NOT NULL DEFAULT 0,
                    image_published INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_ts REAL NOT NULL DEFAULT 0,
                    last_error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbox_fifo ON outbox(id)"
            )
            conn.commit()

    def enqueue(self, event_id: str, event_payload: str, image_path: str) -> None:
        now_ts = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO outbox (
                    event_id, created_ts, event_payload, image_path,
                    event_published, image_published, retry_count, next_retry_ts
                ) VALUES (?, ?, ?, ?, 0, 0, 0, ?)
                """,
                (event_id, now_ts, event_payload, image_path, now_ts),
            )
            conn.commit()

    def peek_ready(self, now_ts: Optional[float] = None) -> Optional[OutboxItem]:
        if now_ts is None:
            now_ts = time.time()

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM outbox
                WHERE next_retry_ts <= ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (now_ts,),
            ).fetchone()

        if row is None:
            return None

        return OutboxItem(
            id=int(row["id"]),
            event_id=str(row["event_id"]),
            event_payload=str(row["event_payload"]),
            image_path=str(row["image_path"]),
            event_published=bool(row["event_published"]),
            image_published=bool(row["image_published"]),
            retry_count=int(row["retry_count"]),
            next_retry_ts=float(row["next_retry_ts"]),
        )

    def mark_event_published(self, row_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbox SET event_published = 1 WHERE id = ?",
                (row_id,),
            )
            conn.commit()

    def mark_image_published(self, row_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbox SET image_published = 1 WHERE id = ?",
                (row_id,),
            )
            conn.commit()

    def complete(self, row_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM outbox WHERE id = ?", (row_id,))
            conn.commit()

    def defer_retry(self, row_id: int, retry_count: int, next_retry_ts: float, error_text: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE outbox
                SET retry_count = ?, next_retry_ts = ?, last_error = ?
                WHERE id = ?
                """,
                (retry_count, next_retry_ts, error_text[:500], row_id),
            )
            conn.commit()

    def count_pending(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM outbox").fetchone()
        return int(row["c"]) if row else 0
