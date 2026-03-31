"""
Session state manager — no Redis required.

h2oGPTe owns conversation history (turns, RAG context) via its conversation_id.
We only store the lightweight phase state + collected fields in-process.

For durability across pod restarts, set STATE_BACKEND=sqlite (default: memory).
For multi-replica deployments, set STATE_BACKEND=external_redis and provide
REDIS_URL pointing to a managed Redis (e.g. AWS ElastiCache / Azure Cache).
"""

import os
import json
import threading
import sqlite3
import tempfile
from datetime import datetime, timedelta

STATE_BACKEND = os.environ.get("STATE_BACKEND", "memory")  # memory | sqlite | external_redis
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "24"))


# ---------------------------------------------------------------------------
# In-memory backend (single replica, no persistence)
# ---------------------------------------------------------------------------
class MemoryStateBackend:
    def __init__(self):
        self._store: dict[str, dict] = {}
        self._expiry: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            if key not in self._store:
                return None
            if datetime.utcnow() > self._expiry[key]:
                del self._store[key]
                del self._expiry[key]
                return None
            return dict(self._store[key])

    def set(self, key: str, value: dict):
        with self._lock:
            self._store[key] = dict(value)
            self._expiry[key] = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)
            self._expiry.pop(key, None)

    def list_all(self) -> list[dict]:
        with self._lock:
            now = datetime.utcnow()
            return [
                v for k, v in self._store.items()
                if self._expiry.get(k, now) > now
            ]


# ---------------------------------------------------------------------------
# SQLite backend (single replica, survives pod restart)
# ---------------------------------------------------------------------------
class SQLiteStateBackend:
    _DB_PATH = os.environ.get("SQLITE_PATH", "/tmp/jbs_sessions.db")

    def __init__(self):
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self._DB_PATH, check_same_thread=False)

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_key TEXT PRIMARY KEY,
                    data        TEXT NOT NULL,
                    expires_at  TEXT NOT NULL
                )
            """)

    def get(self, key: str) -> dict | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT data, expires_at FROM sessions WHERE session_key = ?", (key,)
            ).fetchone()
            if not row:
                return None
            if datetime.utcnow() > datetime.fromisoformat(row[1]):
                conn.execute("DELETE FROM sessions WHERE session_key = ?", (key,))
                return None
            return json.loads(row[0])

    def set(self, key: str, value: dict):
        expires = (datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_key, data, expires_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), expires)
            )

    def delete(self, key: str):
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE session_key = ?", (key,))

    def list_all(self) -> list[dict]:
        now = datetime.utcnow().isoformat()
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT data FROM sessions WHERE expires_at > ?", (now,)
            ).fetchall()
            return [json.loads(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# External Redis backend (multi-replica, managed Redis only — no Helm pod)
# ---------------------------------------------------------------------------
class ExternalRedisStateBackend:
    """
    Use only with a MANAGED Redis service:
      - AWS ElastiCache
      - Azure Cache for Redis
      - GCP Memorystore
    Do NOT deploy a Redis pod via Helm for this.
    Set REDIS_URL=rediss://:password@host:6380/0
    """

    def __init__(self):
        import redis as redis_lib
        url = os.environ["REDIS_URL"]
        self.r = redis_lib.from_url(url, decode_responses=True)
        self._tenant = os.environ.get("TENANT_ID", "certis")

    def _key(self, user_id: str) -> str:
        return f"{self._tenant}:session:{user_id}"

    def get(self, key: str) -> dict | None:
        raw = self.r.get(self._key(key))
        return json.loads(raw) if raw else None

    def set(self, key: str, value: dict):
        self.r.setex(
            self._key(key),
            timedelta(hours=SESSION_TTL_HOURS),
            json.dumps(value)
        )

    def delete(self, key: str):
        self.r.delete(self._key(key))

    def list_all(self) -> list[dict]:
        pattern = f"{self._tenant}:session:*"
        keys = self.r.keys(pattern)
        result = []
        for k in keys:
            raw = self.r.get(k)
            if raw:
                result.append(json.loads(raw))
        return result


# ---------------------------------------------------------------------------
# Public StateManager — selects backend from STATE_BACKEND env var
# ---------------------------------------------------------------------------
def _build_backend():
    if STATE_BACKEND == "sqlite":
        return SQLiteStateBackend()
    elif STATE_BACKEND == "external_redis":
        return ExternalRedisStateBackend()
    else:
        return MemoryStateBackend()


class StateManager:
    """
    Thin wrapper that stores only phase state + collected fields.
    Conversation turn history lives inside h2oGPTe (via conversation_id).
    """

    def __init__(self):
        self._backend = _build_backend()

    def load(self, user_id: str) -> dict:
        session = self._backend.get(user_id)
        if session:
            return session
        return {
            "user_id":          user_id,
            "phase":            1,
            "collected_fields": {},
            # h2oGPTe owns conversation history — we just hold the ID reference
            "h2ogpte_conv_id":  None,
            "collection_id":    None,
            "status":           "active",
        }

    def save(self, user_id: str, session: dict):
        self._backend.set(user_id, session)

    def delete(self, user_id: str):
        self._backend.delete(user_id)

    def list_all(self) -> list[dict]:
        return self._backend.list_all()
