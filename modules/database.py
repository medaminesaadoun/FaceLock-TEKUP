# modules/database.py
import sqlite3
import os
from contextlib import closing
from datetime import datetime, timezone
from typing import Optional


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize(db_path: str) -> None:
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with closing(get_connection(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id               INTEGER PRIMARY KEY,
                windows_username TEXT    UNIQUE NOT NULL,
                consent_timestamp TEXT   NOT NULL,
                consent_version  TEXT    NOT NULL,
                fallback_method  TEXT    NOT NULL,
                pin_hash         TEXT
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                id                  INTEGER PRIMARY KEY,
                user_id             INTEGER REFERENCES users(id),
                encrypted_embedding BLOB    NOT NULL,
                created_at          TEXT    NOT NULL,
                last_used_at        TEXT,
                name                TEXT    DEFAULT 'Primary'
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id               INTEGER PRIMARY KEY,
                timestamp        TEXT NOT NULL,
                windows_username TEXT NOT NULL,
                result           TEXT NOT NULL,
                mode             TEXT NOT NULL
            );
        """)
        # Migration: add name column for databases created before multi-user support.
        try:
            conn.execute("ALTER TABLE embeddings ADD COLUMN name TEXT DEFAULT 'Primary'")
            conn.commit()
        except Exception:
            pass  # Column already exists


def check_integrity(db_path: str) -> bool:
    with closing(get_connection(db_path)) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return row[0] == "ok"


def add_user(db_path: str, username: str, consent_ts: str,
             consent_version: str, fallback: str, pin_hash: Optional[str]) -> int:
    with closing(get_connection(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO users (windows_username, consent_timestamp, consent_version, fallback_method, pin_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, consent_ts, consent_version, fallback, pin_hash)
        )
        conn.commit()
        return cur.lastrowid


def get_user(db_path: str, username: str) -> Optional[dict]:
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE windows_username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def update_user_fallback(db_path: str, username: str,
                         fallback: str, pin_hash: Optional[str]) -> None:
    """Update fallback method and PIN hash for an already-enrolled user.
    Called during re-enrollment when the user changes their fallback choice."""
    with closing(get_connection(db_path)) as conn:
        conn.execute(
            "UPDATE users SET fallback_method = ?, pin_hash = ? "
            "WHERE windows_username = ?",
            (fallback, pin_hash, username)
        )
        conn.commit()


def save_embedding(db_path: str, user_id: int, encrypted_embedding: bytes,
                   name: str = "Primary") -> None:
    """Re-enroll: replace ALL embeddings for this user with one new one."""
    now = datetime.now(timezone.utc).isoformat()
    with closing(get_connection(db_path)) as conn:
        conn.execute("DELETE FROM embeddings WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO embeddings (user_id, encrypted_embedding, created_at, name) "
            "VALUES (?, ?, ?, ?)",
            (user_id, encrypted_embedding, now, name)
        )
        conn.commit()


def add_embedding(db_path: str, user_id: int, encrypted_embedding: bytes,
                  name: str = "User") -> None:
    """Add a new embedding without removing existing ones (multi-user support)."""
    now = datetime.now(timezone.utc).isoformat()
    with closing(get_connection(db_path)) as conn:
        conn.execute(
            "INSERT INTO embeddings (user_id, encrypted_embedding, created_at, name) "
            "VALUES (?, ?, ?, ?)",
            (user_id, encrypted_embedding, now, name)
        )
        conn.commit()


def get_embeddings(db_path: str, user_id: int) -> list[tuple[int, bytes, str]]:
    """Return all (id, encrypted_embedding, name) tuples for a user."""
    with closing(get_connection(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, encrypted_embedding, name FROM embeddings WHERE user_id = ?",
            (user_id,)
        ).fetchall()
        return [(r["id"], bytes(r["encrypted_embedding"]), r["name"] or "User")
                for r in rows]


def get_embedding(db_path: str, user_id: int) -> Optional[bytes]:
    """Return the first stored embedding blob (legacy single-user access)."""
    with closing(get_connection(db_path)) as conn:
        row = conn.execute(
            "SELECT encrypted_embedding FROM embeddings WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bytes(row["encrypted_embedding"]) if row else None


def delete_embedding_by_id(db_path: str, embedding_id: int) -> None:
    """Delete a specific enrolled face by its embedding row id."""
    with closing(get_connection(db_path)) as conn:
        conn.execute("DELETE FROM embeddings WHERE id = ?", (embedding_id,))
        conn.commit()


def update_last_used(db_path: str, user_id: int) -> None:
    with closing(get_connection(db_path)) as conn:
        conn.execute(
            "UPDATE embeddings SET last_used_at = ? WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id)
        )
        conn.commit()


def log_auth_event(db_path: str, username: str, result: str, mode: str) -> None:
    with closing(get_connection(db_path)) as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, windows_username, result, mode) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), username, result, mode)
        )
        conn.commit()


def erase_user(db_path: str, username: str) -> None:
    with closing(get_connection(db_path)) as conn:
        user = conn.execute(
            "SELECT id FROM users WHERE windows_username = ?", (username,)
        ).fetchone()
        if not user:
            return
        conn.execute("DELETE FROM embeddings WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM audit_log WHERE windows_username = ?", (username,))
        conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        conn.commit()
