"""SQLite-backed conversation + message persistence with a status machine.

Conversation status machine:  bot -> queued -> human -> resolved
  - bot:      AI is auto-answering
  - queued:   escalated, waiting for a human agent to claim
  - human:    a human agent has taken over
  - resolved: conversation closed

This replaces the in-memory-only session model for the customer-facing chat so
that history survives restarts and is visible to human agents.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


STATUSES = ("bot", "queued", "human", "resolved")
PRIORITY = {"low": 1, "medium": 2, "high": 3, "urgent": 4}


class ConversationStore:
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
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id  TEXT PRIMARY KEY,
                    customer_id      TEXT NOT NULL,
                    channel          TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    assigned_agent   TEXT,
                    priority         INTEGER NOT NULL DEFAULT 2,
                    escalation_reason TEXT,
                    csat             INTEGER,
                    created_at       REAL NOT NULL,
                    updated_at       REAL NOT NULL,
                    last_message_at  REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conv_customer ON conversations(customer_id);
                CREATE INDEX IF NOT EXISTS idx_conv_status ON conversations(status);

                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role            TEXT NOT NULL,   -- customer | ai | agent | system
                    sender          TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    meta_json       TEXT NOT NULL DEFAULT '{}',
                    created_at      REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
                """
            )
            conn.commit()

    # ----------------------------------------------------------------- rows
    @staticmethod
    def _conv_row(row) -> Dict[str, Any]:
        return {
            "conversation_id": row["conversation_id"],
            "customer_id": row["customer_id"],
            "channel": row["channel"],
            "status": row["status"],
            "assigned_agent": row["assigned_agent"],
            "priority": int(row["priority"]),
            "escalation_reason": row["escalation_reason"],
            "csat": row["csat"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "last_message_at": float(row["last_message_at"]),
        }

    @staticmethod
    def _msg_row(row) -> Dict[str, Any]:
        return {
            "id": int(row["id"]),
            "conversation_id": row["conversation_id"],
            "role": row["role"],
            "sender": row["sender"],
            "content": row["content"],
            "meta": json.loads(row["meta_json"] or "{}"),
            "created_at": float(row["created_at"]),
        }

    # -------------------------------------------------------- conversations
    def get_or_create_active(self, customer_id: str, channel: str = "web") -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM conversations
                   WHERE customer_id = ? AND status != 'resolved'
                   ORDER BY created_at DESC LIMIT 1""",
                (customer_id,),
            ).fetchone()
            if row is not None:
                return self._conv_row(row)
            now = time.time()
            cid = f"C{uuid.uuid4().hex[:12].upper()}"
            conn.execute(
                """INSERT INTO conversations(conversation_id, customer_id, channel,
                   status, priority, created_at, updated_at, last_message_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (cid, customer_id, channel, "bot", 2, now, now, now),
            )
            conn.commit()
            return self.get_conversation(cid)

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        return self._conv_row(row) if row else None

    def list_conversations(
        self, status: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if status and status != "all":
                rows = conn.execute(
                    """SELECT * FROM conversations WHERE status = ?
                       ORDER BY priority DESC, last_message_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM conversations
                       ORDER BY priority DESC, last_message_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
        return [self._conv_row(r) for r in rows]

    def _touch(self, conn, conversation_id: str, message_time: bool = False) -> None:
        now = time.time()
        if message_time:
            conn.execute(
                "UPDATE conversations SET updated_at = ?, last_message_at = ? WHERE conversation_id = ?",
                (now, now, conversation_id),
            )
        else:
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )

    def set_status(
        self,
        conversation_id: str,
        status: str,
        assigned_agent: Optional[str] = None,
        escalation_reason: Optional[str] = None,
        priority: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        with self._connect() as conn:
            sets = ["status = ?"]
            params: List[Any] = [status]
            if assigned_agent is not None:
                sets.append("assigned_agent = ?")
                params.append(assigned_agent or None)
            if escalation_reason is not None:
                sets.append("escalation_reason = ?")
                params.append(escalation_reason)
            if priority is not None:
                sets.append("priority = ?")
                params.append(priority)
            params.append(conversation_id)
            conn.execute(
                f"UPDATE conversations SET {', '.join(sets)} WHERE conversation_id = ?",
                params,
            )
            self._touch(conn, conversation_id)
            conn.commit()
        return self.get_conversation(conversation_id)

    def assign_agent(self, conversation_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
        return self.set_status(conversation_id, "human", assigned_agent=agent_id)

    def release_to_bot(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self.set_status(conversation_id, "bot", assigned_agent="")

    def restore_snapshot(
        self,
        conversation: Dict[str, Any],
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Restore a browser-held conversation snapshot into this store.

        Vercel serverless instances do not share ``/tmp`` SQLite files. The
        agent console may fetch a queued chat from one instance, then send the
        claim to another fresh instance. This method lets privileged agent
        actions rehydrate that known conversation before applying the action.
        """
        conversation_id = str(conversation.get("conversation_id") or "").strip()
        customer_id = str(conversation.get("customer_id") or "").strip()
        if not conversation_id or not customer_id:
            return None

        status = str(conversation.get("status") or "queued")
        if status not in STATUSES:
            status = "queued"
        now = time.time()

        def _float_value(key: str, default: float) -> float:
            try:
                return float(conversation.get(key) or default)
            except (TypeError, ValueError):
                return default

        try:
            priority = int(conversation.get("priority") or 2)
        except (TypeError, ValueError):
            priority = 2

        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO conversations(
                   conversation_id, customer_id, channel, status, assigned_agent,
                   priority, escalation_reason, csat, created_at, updated_at, last_message_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    conversation_id,
                    customer_id,
                    str(conversation.get("channel") or "web"),
                    status,
                    conversation.get("assigned_agent") or None,
                    priority,
                    conversation.get("escalation_reason"),
                    conversation.get("csat"),
                    _float_value("created_at", now),
                    _float_value("updated_at", now),
                    _float_value("last_message_at", now),
                ),
            )
            existing_messages = conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if int(existing_messages["c"]) == 0:
                for item in messages or []:
                    role = str(item.get("role") or "")
                    content = str(item.get("content") or "")
                    if role not in {"customer", "ai", "agent", "system"} or not content:
                        continue
                    try:
                        created_at = float(item.get("created_at") or now)
                    except (TypeError, ValueError):
                        created_at = now
                    conn.execute(
                        """INSERT INTO messages(conversation_id, role, sender, content, meta_json, created_at)
                           VALUES (?,?,?,?,?,?)""",
                        (
                            conversation_id,
                            role,
                            str(item.get("sender") or role),
                            content,
                            json.dumps(item.get("meta") or {}, ensure_ascii=False),
                            created_at,
                        ),
                    )
            conn.commit()
        return self.get_conversation(conversation_id)

    def set_csat(self, conversation_id: str, csat: int) -> Optional[Dict[str, Any]]:
        """Record the overall satisfaction score without changing status."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET csat = ?, updated_at = ? WHERE conversation_id = ?",
                (csat, time.time(), conversation_id),
            )
            conn.commit()
        return self.get_conversation(conversation_id)

    def resolve(self, conversation_id: str, csat: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET status='resolved', csat=?, updated_at=? WHERE conversation_id=?",
                (csat, time.time(), conversation_id),
            )
            conn.commit()
        return self.get_conversation(conversation_id)

    # --------------------------------------------------------------- messages
    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        sender: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO messages(conversation_id, role, sender, content, meta_json, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (conversation_id, role, sender or role, content,
                 json.dumps(meta or {}, ensure_ascii=False), now),
            )
            msg_id = cur.lastrowid
            self._touch(conn, conversation_id, message_time=True)
            conn.commit()
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        return self._msg_row(row)

    def list_messages(self, conversation_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM messages WHERE conversation_id = ?
                   ORDER BY id ASC LIMIT ?""",
                (conversation_id, limit),
            ).fetchall()
        return [self._msg_row(r) for r in rows]

    def recent_customer_messages(self, conversation_id: str, limit: int = 6) -> List[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT content FROM messages
                   WHERE conversation_id = ? AND role = 'customer'
                   ORDER BY id DESC LIMIT ?""",
                (conversation_id, limit),
            ).fetchall()
        return [r["content"] for r in rows]

    def counts_by_status(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS c FROM conversations GROUP BY status"
            ).fetchall()
        return {r["status"]: int(r["c"]) for r in rows}
