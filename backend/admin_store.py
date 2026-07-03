"""SQLite-backed admin authentication for the demo backoffice."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional


class AdminStore:
    """Small local admin store with hashed passwords and bearer sessions."""

    def __init__(
        self,
        db_path: str,
        default_username: str = "admin",
        default_password: str = "Admin@2026!",
        session_ttl_seconds: int = 86400,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_username = default_username.strip() or "admin"
        self.default_password = default_password or "Admin@2026!"
        self.session_ttl_seconds = max(300, int(session_ttl_seconds))
        self._init_db()
        self._ensure_default_admin()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS admin_users (
                    username          TEXT PRIMARY KEY,
                    password_hash     TEXT NOT NULL,
                    role              TEXT NOT NULL,
                    display_name      TEXT NOT NULL,
                    created_at        REAL NOT NULL,
                    last_login_at     REAL
                );

                CREATE TABLE IF NOT EXISTS admin_sessions (
                    token             TEXT PRIMARY KEY,
                    username          TEXT NOT NULL,
                    created_at        REAL NOT NULL,
                    expires_at        REAL NOT NULL,
                    FOREIGN KEY(username) REFERENCES admin_users(username)
                );
                CREATE INDEX IF NOT EXISTS idx_admin_sessions_user ON admin_sessions(username);
                CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires ON admin_sessions(expires_at);
                """
            )
            conn.commit()

    @staticmethod
    def _hash_password(password: str, salt: Optional[bytes] = None, iterations: int = 200_000) -> str:
        salt = salt or secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return "pbkdf2_sha256${}${}${}".format(
            iterations,
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )

    @classmethod
    def _verify_password(cls, password: str, encoded: str) -> bool:
        try:
            algorithm, iterations_raw, salt_raw, digest_raw = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(iterations_raw)
            salt = base64.b64decode(salt_raw.encode("ascii"))
            expected = base64.b64decode(digest_raw.encode("ascii"))
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    @staticmethod
    def _user_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "username": row["username"],
            "role": row["role"],
            "display_name": row["display_name"],
            "created_at": float(row["created_at"]),
            "last_login_at": float(row["last_login_at"]) if row["last_login_at"] is not None else None,
        }

    def _ensure_default_admin(self) -> None:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT username FROM admin_users WHERE username = ?",
                (self.default_username,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO admin_users(username, password_hash, role, display_name, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        self.default_username,
                        self._hash_password(self.default_password),
                        "admin",
                        "系统管理员",
                        now,
                    ),
                )
                conn.commit()

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        username = username.strip()
        if not username or not password:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM admin_users WHERE username = ?",
                (username,),
            ).fetchone()
            if row is None or not self._verify_password(password, row["password_hash"]):
                return None

            now = time.time()
            token = secrets.token_urlsafe(32)
            expires_at = now + self.session_ttl_seconds
            conn.execute(
                "DELETE FROM admin_sessions WHERE expires_at <= ?",
                (now,),
            )
            conn.execute(
                """INSERT INTO admin_sessions(token, username, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (token, username, now, expires_at),
            )
            conn.execute(
                "UPDATE admin_users SET last_login_at = ? WHERE username = ?",
                (now, username),
            )
            conn.commit()

            user_row = conn.execute(
                "SELECT * FROM admin_users WHERE username = ?",
                (username,),
            ).fetchone()

        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": expires_at,
            "user": self._user_row(user_row),
        }

    def get_session(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        now = time.time()
        with self._connect() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now,))
            row = conn.execute(
                """SELECT s.token, s.created_at, s.expires_at, u.*
                   FROM admin_sessions s
                   JOIN admin_users u ON u.username = s.username
                   WHERE s.token = ?""",
                (token,),
            ).fetchone()
            conn.commit()
        if row is None or float(row["expires_at"]) <= now:
            return None
        return {
            "token": row["token"],
            "created_at": float(row["created_at"]),
            "expires_at": float(row["expires_at"]),
            "user": self._user_row(row),
        }

    def logout(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE token = ?", (token,))
            conn.commit()

    def list_users(self) -> list[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT username, role, display_name, created_at, last_login_at
                   FROM admin_users ORDER BY created_at ASC"""
            ).fetchall()
        return [self._user_row(row) for row in rows]
