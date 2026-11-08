"""
Hosted database (Supabase Postgres, provisioned through the Vercel integration).

Two jobs:
  1. Source of truth for the data ALICE answers from (courses, minors, baskets, BMS spread, campus rooms, equipment,
     facilities). At start-up, and then every SYNC_EVERY_S, the tables are copied into a local SQLite cache that every
     exact lookup reads. Reads therefore stay fast, an edit in Supabase reaches answers within minutes, and if
     Supabase is unreachable the bundled copy keeps answering.
  2. A chat log: one row per question, written after the safety layer has removed phone numbers, emails and ID
     numbers. The student ID is never stored. Row-level security is on with no policies, so Supabase's public REST
     API cannot read these tables; only this server's database role can.

Without POSTGRES_URL (local runs, tests) everything here is a no-op and the bundled SQLite file is used.
Load or refresh the hosted tables from the bundled file:  python load_supabase.py
"""
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

DATABASE_URL = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
SYNC_EVERY_S = float(os.getenv("DB_SYNC_EVERY_S", "600"))
CACHE = Path(tempfile.gettempdir()) / "alice_courses_live.db"

# table -> (columns in order, primary key or None). The same shape in SQLite and Postgres.
TABLES = {
    "courses": ["id", "kind", "batch", "semester", "basket", "minor", "course_code", "code_status", "title", "L", "T", "P",
                "credits", "prerequisites_raw", "prerequisites_normalized", "prerequisite_codes", "choice_of",
                "source_file", "source_tab", "source_row"],
    "baskets": ["batch", "basket", "min_credits", "placed_credits", "source_file", "source_tab", "source_row"],
    "struct_baskets": ["batch", "basket", "credits", "source_file", "source_tab", "source_row"],
    "bms_courses": ["semester", "basket", "code", "title", "L", "T", "P", "credits", "timetable_batch", "source_row"],
    "bms_baskets": ["basket", "min_credits", "captured_credits", "source_row"],
    "rooms": ["room_id", "block", "floor", "floor_name", "name", "type", "department", "capacity", "ac", "ac_units", "source_row"],
    "equipment": ["equipment_id", "room_id", "block", "floor", "item", "category", "quantity", "operational_qty", "status",
                  "source_row"],
    "facilities": ["facility_id", "block", "floor", "type", "name", "location", "quantity", "operational_qty", "status",
                   "details", "source_row"],
    "infra_summary": ["metric", "block_a", "block_b", "block_c", "total", "source_row"],
    "infra_rules": ["section", "text", "source_row"],
}

_state = {"source": "bundled", "synced_at": None, "error": None, "rows": 0}
_lock = threading.Lock()
_conn = None


def _q(name: str) -> str:
    return f'"{name.lower()}"'  # L, T, P are upper case in SQLite; Postgres folds them, so quote consistently


def connect():
    """One connection per server instance, reopened if it drops. Supabase's pooler runs in transaction mode, so
    prepared statements are switched off."""
    global _conn
    import psycopg  # noqa: PLC0415 - only needed when a hosted database is configured
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(DATABASE_URL, connect_timeout=4, autocommit=True, prepare_threshold=None)
    return _conn


def status() -> dict:
    return dict(_state, configured=bool(DATABASE_URL))


def sync(bundled: Path) -> Path:
    """Copy the hosted tables into the local cache; returns the SQLite file the lookups should read."""
    if not DATABASE_URL:
        return bundled
    try:
        with _lock:
            tmp = CACHE.with_suffix(".tmp")
            shutil.copy2(bundled, tmp)  # same schema and indexes; the hosted rows replace the bundled ones
            lite = sqlite3.connect(tmp)
            total = 0
            with connect().cursor() as cur:
                for table, cols in TABLES.items():
                    cur.execute(f"SELECT {', '.join(_q(c) for c in cols)} FROM {table}")
                    rows = cur.fetchall()
                    if not rows:
                        raise RuntimeError(f"hosted table {table} is empty")
                    lite.execute(f"DELETE FROM {table}")
                    lite.executemany(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
                    total += len(rows)
            lite.commit()
            lite.close()
            os.replace(tmp, CACHE)
            _state.update(source="supabase", synced_at=time.time(), error=None, rows=total)
        return CACHE
    except Exception as e:  # noqa: BLE001 - keep answering from the last good copy
        _state.update(error=f"{type(e).__name__}: {str(e)[:120]}")
        print(f"[db] sync failed, using {'last sync' if CACHE.exists() and _state['synced_at'] else 'bundled copy'}: {e}")
        return CACHE if CACHE.exists() and _state["synced_at"] else bundled


def due() -> bool:
    return bool(DATABASE_URL) and (_state["synced_at"] is None or time.time() - _state["synced_at"] > SYNC_EVERY_S)


LOG_SQL = """INSERT INTO chat_log (strategy, question, answer, provider, total_tokens, cost_usd, response_time_s,
    needs_clarification, insufficient_information, guard, had_profile, sources)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""


def log_chat(question: str, reply, had_profile: bool) -> None:
    """question must already be scrubbed by the safety layer. Failures never reach the student."""
    if not DATABASE_URL:
        return
    try:
        with connect().cursor() as cur:
            cur.execute(LOG_SQL, (reply.strategy, question[:2000], reply.answer[:8000], reply.provider,
                                  reply.usage.total_tokens, reply.usage.estimated_cost_usd, round(reply.response_time_s, 3),
                                  reply.needs_clarification, reply.insufficient_information, reply.guard, had_profile,
                                  "; ".join(f"{s.source_file} | {s.section}" for s in reply.sources)[:2000]))
    except Exception as e:  # noqa: BLE001
        print(f"[db] chat log failed: {type(e).__name__}: {str(e)[:120]}")
