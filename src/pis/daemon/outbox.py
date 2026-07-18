from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OutboxItem:
    id: int
    body: dict


class Outbox:
    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS outbox ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " body TEXT NOT NULL,"
            " created_at REAL NOT NULL,"
            " sent_at REAL)"
        )
        self.conn.commit()

    def enqueue(self, body: dict) -> int:
        cursor = self.conn.execute(
            "INSERT INTO outbox (body, created_at) VALUES (?, ?)",
            (json.dumps(body), time.time()),
        )
        self.conn.commit()
        return cursor.lastrowid

    def pending(self) -> list[OutboxItem]:
        rows = self.conn.execute(
            "SELECT id, body FROM outbox WHERE sent_at IS NULL ORDER BY id"
        ).fetchall()
        return [OutboxItem(row[0], json.loads(row[1])) for row in rows]

    def mark_sent(self, item_id: int) -> None:
        self.conn.execute("UPDATE outbox SET sent_at = ? WHERE id = ?", (time.time(), item_id))
        self.conn.commit()

    def flush(self, post_fn: Callable[[dict], bool]) -> int:
        sent = 0
        for item in self.pending():
            if not post_fn(item.body):
                break
            self.mark_sent(item.id)
            sent += 1
        return sent
