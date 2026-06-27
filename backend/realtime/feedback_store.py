"""SQLite-backed user feedback collection for model improvement.

Two granularities:
  - message_feedback:     per AI reply thumbs up / down (fine-grained training signal)
  - conversation_ratings: end-of-chat CSAT (1-5 stars + reason tags + free comment)

The aggregate ``summary()`` is meant for an analytics/backoffice view and for
building preference datasets later.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional


# Suggested low-score reason tags surfaced by the rating form.
SUGGESTED_TAGS = [
    "没有解决问题",
    "答非所问",
    "回答太慢",
    "态度不好",
    "重复啰嗦",
    "信息不准确",
    "转人工太慢",
]


class FeedbackStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        import sqlite3

        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS message_feedback (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    message_id      INTEGER NOT NULL,
                    customer_id     TEXT NOT NULL,
                    value           TEXT NOT NULL,   -- 'up' | 'down'
                    created_at      REAL NOT NULL,
                    UNIQUE(conversation_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_mfb_conv ON message_feedback(conversation_id);

                CREATE TABLE IF NOT EXISTS conversation_ratings (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL UNIQUE,
                    customer_id     TEXT NOT NULL,
                    stars           INTEGER NOT NULL,
                    tags_json       TEXT NOT NULL DEFAULT '[]',
                    comment         TEXT NOT NULL DEFAULT '',
                    created_at      REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rate_conv ON conversation_ratings(conversation_id);
                """
            )
            conn.commit()

    # ----------------------------------------------------- per-message thumbs
    def add_message_feedback(
        self, conversation_id: str, message_id: int, customer_id: str, value: str
    ) -> Dict[str, Any]:
        if value not in ("up", "down"):
            raise ValueError("value must be 'up' or 'down'")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO message_feedback(conversation_id, message_id, customer_id, value, created_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(conversation_id, message_id)
                   DO UPDATE SET value=excluded.value, created_at=excluded.created_at""",
                (conversation_id, message_id, customer_id, value, now),
            )
            conn.commit()
        return {"conversation_id": conversation_id, "message_id": message_id, "value": value}

    # -------------------------------------------------------- conversation CSAT
    def add_rating(
        self,
        conversation_id: str,
        customer_id: str,
        stars: int,
        tags: Optional[List[str]] = None,
        comment: str = "",
    ) -> Dict[str, Any]:
        stars = max(1, min(5, int(stars)))
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO conversation_ratings(conversation_id, customer_id, stars, tags_json, comment, created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(conversation_id)
                   DO UPDATE SET stars=excluded.stars, tags_json=excluded.tags_json,
                                 comment=excluded.comment, created_at=excluded.created_at""",
                (conversation_id, customer_id, stars,
                 json.dumps(tags or [], ensure_ascii=False), comment or "", now),
            )
            conn.commit()
        return {"conversation_id": conversation_id, "stars": stars, "tags": tags or [], "comment": comment}

    def get_rating(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_ratings WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "conversation_id": row["conversation_id"],
            "customer_id": row["customer_id"],
            "stars": int(row["stars"]),
            "tags": json.loads(row["tags_json"] or "[]"),
            "comment": row["comment"],
            "created_at": float(row["created_at"]),
        }

    def list_ratings(self, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conversation_ratings ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "conversation_id": r["conversation_id"],
                "customer_id": r["customer_id"],
                "stars": int(r["stars"]),
                "tags": json.loads(r["tags_json"] or "[]"),
                "comment": r["comment"],
                "created_at": float(r["created_at"]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------- analytics
    def summary(self) -> Dict[str, Any]:
        with self._connect() as conn:
            rrows = conn.execute("SELECT stars, tags_json FROM conversation_ratings").fetchall()
            mrows = conn.execute("SELECT value FROM message_feedback").fetchall()

        dist = {str(i): 0 for i in range(1, 6)}
        tag_counter: Counter = Counter()
        for r in rrows:
            dist[str(int(r["stars"]))] += 1
            for t in json.loads(r["tags_json"] or "[]"):
                tag_counter[t] += 1
        count = len(rrows)
        avg = round(sum(int(r["stars"]) for r in rrows) / count, 2) if count else 0.0

        up = sum(1 for m in mrows if m["value"] == "up")
        down = sum(1 for m in mrows if m["value"] == "down")
        total = up + down

        return {
            "ratings": {"count": count, "avg_stars": avg, "distribution": dist},
            "messages": {
                "up": up,
                "down": down,
                "total": total,
                "satisfaction": round(up / total, 3) if total else None,
            },
            "top_tags": [{"tag": t, "count": c} for t, c in tag_counter.most_common(10)],
        }
