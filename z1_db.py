"""
z1_db
Database interface for Z1 silo persistence.

Role:
    Replaces filesystem journal with durable Postgres storage.
    All silo reads and writes go through here.
    Deduplication enforced at the database level via digest uniqueness.
"""

import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone


DATABASE_URL = os.environ.get("DATABASE_URL")


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS silo_operational (
                    id SERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    session_id TEXT,
                    source TEXT,
                    kind TEXT,
                    content TEXT,
                    kernels JSONB,
                    tags JSONB,
                    confidence FLOAT,
                    digest TEXT UNIQUE
                );
            """)
        conn.commit()


def write_silo(
    content: str,
    *,
    kind: str = "event",
    source: str = "bridge",
    session_id: str = "",
    kernels: list = None,
    tags: list = None,
    confidence: float = 0.0,
    digest: str = "",
) -> bool:
    """
    Write a record to silo_operational.
    Returns True if written, False if duplicate (digest conflict).
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO silo_operational
                        (session_id, source, kind, content, kernels, tags, confidence, digest)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (digest) DO NOTHING
                """, (
                    session_id,
                    source,
                    kind,
                    content,
                    json.dumps(kernels or []),
                    json.dumps(tags or []),
                    confidence,
                    digest,
                ))
            conn.commit()
            return True
    except Exception as e:
        print(f"SILO_WRITE_ERROR: {e}")
        return False


def read_silo(
    *,
    limit: int = 20,
    kind: str = None,
    source: str = None,
) -> list[dict]:
    """Read recent records from silo_operational."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                filters = []
                params = []
                if kind:
                    filters.append("kind = %s")
                    params.append(kind)
                if source:
                    filters.append("source = %s")
                    params.append(source)
                where = ("WHERE " + " AND ".join(filters)) if filters else ""
                params.append(limit)
                cur.execute(f"""
                    SELECT * FROM silo_operational
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                """, params)
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"SILO_READ_ERROR: {e}")
        return []
