"""SQLite-хранилище кампаний, результатов и рабочих задач."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")



def target_key(target: tuple[str, str | int]) -> str:
    kind, value = target
    return f"{kind}:{value}"


class CampaignStore:
    """Синхронный SQLite-store для одного asyncio-процесса бота."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._init_schema()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'scheduled',
                total INTEGER NOT NULL DEFAULT 0,
                sent INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                scheduled_for TEXT,
                started_at TEXT,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS campaign_targets (
                campaign_id INTEGER NOT NULL,
                target_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (campaign_id, target_key),
                FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS work_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_campaigns_owner_created
                ON campaigns(owner_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_owner_updated
                ON work_tasks(owner_id, updated_at DESC);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------- кампании ----------
    def create_campaign(
        self,
        owner_id: int,
        message_text: str,
        targets: Iterable[tuple[str, str | int]],
        *,
        dry_run: bool = False,
        scheduled_for: str | None = None,
    ) -> int:
        unique_targets = list(dict.fromkeys(targets))
        now = utc_now()
        status = "scheduled" if scheduled_for else "running"
        cur = self.conn.execute(
            """
            INSERT INTO campaigns(
                owner_id, message_text, dry_run, status, total,
                created_at, scheduled_for
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (owner_id, message_text, int(dry_run), status, len(unique_targets), now, scheduled_for),
        )
        campaign_id = int(cur.lastrowid)
        self.conn.executemany(
            """
            INSERT OR IGNORE INTO campaign_targets(
                campaign_id, target_key, kind, value, status, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            [
                (campaign_id, target_key(t), t[0], str(t[1]), now)
                for t in unique_targets
            ],
        )
        self.conn.commit()
        return campaign_id

    def mark_started(self, campaign_id: int) -> None:
        self.conn.execute(
            """
            UPDATE campaigns
            SET status = 'running', started_at = COALESCE(started_at, ?)
            WHERE id = ?
            """,
            (utc_now(), campaign_id),
        )
        self.conn.commit()

    def record_target(
        self,
        campaign_id: int,
        target: tuple[str, str | int],
        status: str,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE campaign_targets
            SET status = ?, error = ?, updated_at = ?
            WHERE campaign_id = ? AND target_key = ?
            """,
            (status, error, utc_now(), campaign_id, target_key(target)),
        )
        self.conn.commit()

    def finish(
        self,
        campaign_id: int,
        status: str,
        *,
        sent: int = 0,
        skipped: int = 0,
        failed: int = 0,
    ) -> None:
        now = utc_now()
        if status == "cancelled":
            self.conn.execute(
                """
                UPDATE campaign_targets
                SET status = 'cancelled', updated_at = ?
                WHERE campaign_id = ? AND status = 'pending'
                """,
                (now, campaign_id),
            )
        self.conn.execute(
            """
            UPDATE campaigns
            SET status = ?, sent = ?, skipped = ?, failed = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, sent, skipped, failed, now, campaign_id),
        )
        self.conn.commit()

    def recent(self, owner_id: int, limit: int = 10) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            """
            SELECT id, status, dry_run, total, sent, skipped, failed,
                   created_at, scheduled_for, started_at, finished_at
            FROM campaigns
            WHERE owner_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (owner_id, max(1, min(limit, 50))),
        )
        return list(cur.fetchall())

    def get(self, campaign_id: int) -> sqlite3.Row | None:
        cur = self.conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        return cur.fetchone()

    # ---------- рабочие задачи ----------
    def create_task(self, owner_id: int, title: str) -> int:
        now = utc_now()
        cur = self.conn.execute(
            """
            INSERT INTO work_tasks(owner_id, title, status, created_at, updated_at)
            VALUES (?, ?, 'open', ?, ?)
            """,
            (owner_id, title, now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_tasks(self, owner_id: int, limit: int = 20) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            """
            SELECT id, title, status, created_at, updated_at
            FROM work_tasks
            WHERE owner_id = ?
            ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id DESC
            LIMIT ?
            """,
            (owner_id, max(1, min(limit, 100))),
        )
        return list(cur.fetchall())

    def complete_task(self, owner_id: int, task_id: int) -> bool:
        cur = self.conn.execute(
            """
            UPDATE work_tasks
            SET status = 'done', updated_at = ?
            WHERE id = ? AND owner_id = ? AND status != 'done'
            """,
            (utc_now(), task_id, owner_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def delete_task(self, owner_id: int, task_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM work_tasks WHERE id = ? AND owner_id = ?",
            (task_id, owner_id),
        )
        self.conn.commit()
        return cur.rowcount > 0
