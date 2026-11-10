"""
Hosted database (Supabase Postgres, provisioned through the Vercel integration).

Two jobs:
  1. Source of truth for the data ALICE answers from (courses, minors, baskets, campus rooms, equipment,
     facilities). At start-up, and then every SYNC_EVERY_S, the tables are copied into a local SQLite cache that every
     exact lookup reads. Reads therefore stay fast, an edit in Supabase reaches answers within minutes, and if
     Supabase is unreachable the bundled copy keeps answering.
  2. A chat log: one row per question, written after the safety layer has removed phone numbers, emails and ID
     numbers. The student ID is never stored. Row-level security is on with no policies, so Supabase's public REST
     API cannot read these tables; only this server's database role can.

Without POSTGRES_URL (local runs, tests) everything here is a no-op and the bundled SQLite file is used.
The backend sets Supabase up by itself: on start-up, if the hosted tables are missing or were loaded from an older
bundled file (compared by checksum), it creates them, copies the bundled rows in, checks the counts and enables
row-level security. python load_supabase.py does the same by hand.
"""
import hashlib
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

LIBPQ_PARAMS = {"sslmode", "connect_timeout", "application_name", "options", "sslrootcert", "target_session_attrs"}


def _clean_url(url: str | None) -> str | None:
    """Vercel's Supabase URL carries non-libpq query settings (e.g. supa=base-pooler.x) that psycopg rejects."""
    if not url:
        return url
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit  # noqa: PLC0415
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k in LIBPQ_PARAMS]
    if not any(k == "sslmode" for k, _ in query):
        query.append(("sslmode", "require"))
    return urlunsplit(parts._replace(query=urlencode(query)))


DATABASE_URL = _clean_url(os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL"))
SYNC_EVERY_S = float(os.getenv("DB_SYNC_EVERY_S", "600"))
CACHE = Path(tempfile.gettempdir()) / "alice_courses_live.db"

RETIRED_TABLES = ["bms_courses", "bms_baskets"]
# table -> (columns in order, primary key or None). The same shape in SQLite and Postgres.
TABLES = {
    "courses": ["id", "kind", "batch", "semester", "basket", "minor", "course_code", "code_status", "title", "L", "T", "P",
                "credits", "prerequisites_raw", "prerequisites_normalized", "prerequisite_codes", "choice_of",
                "source_file", "source_tab", "source_row"],
    "baskets": ["batch", "basket", "min_credits", "placed_credits", "source_file", "source_tab", "source_row"],
    "struct_baskets": ["batch", "basket", "credits", "source_file", "source_tab", "source_row"],
    "rooms": ["room_id", "block", "floor", "floor_name", "name", "type", "department", "capacity", "ac", "ac_units", "source_row"],
    "equipment": ["equipment_id", "room_id", "block", "floor", "item", "category", "quantity", "operational_qty", "status",
                  "source_row"],
    "facilities": ["facility_id", "block", "floor", "type", "name", "location", "quantity", "operational_qty", "status",
                   "details", "source_row"],
    "infra_summary": ["metric", "block_a", "block_b", "block_c", "total", "source_row"],
    "infra_rules": ["section", "text", "source_row"],
}

_state = {"source": "bundled", "synced_at": None, "error": None, "rows": 0, "seeded_at": None}
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


PG_TYPES = {"INTEGER": "bigint", "REAL": "double precision", "TEXT": "text"}
CHAT_LOG = """CREATE TABLE IF NOT EXISTS chat_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    strategy text, question text, answer text, provider text, total_tokens integer, cost_usd double precision,
    response_time_s double precision, needs_clarification boolean, insufficient_information boolean, guard text,
    had_profile boolean, sources text)"""


def _checksum(bundled: Path) -> str:
    return hashlib.sha256(bundled.read_bytes()).hexdigest()[:16]


def seed(bundled: Path, force: bool = False) -> bool:
    """Create and load the hosted tables from the bundled file when they are missing or out of date.
    Returns True if it loaded. Everything runs in one transaction, so a failure leaves the old data in place."""
    import psycopg  # noqa: PLC0415
    want = _checksum(bundled)
    with psycopg.connect(DATABASE_URL, connect_timeout=6, prepare_threshold=None) as pg, pg.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS alice_meta (key text PRIMARY KEY, value text)")
        cur.execute("ALTER TABLE alice_meta ENABLE ROW LEVEL SECURITY")
        cur.execute("SELECT value FROM alice_meta WHERE key='data_checksum'")
        have = cur.fetchone()
        cur.execute(CHAT_LOG)
        cur.execute("ALTER TABLE chat_log ENABLE ROW LEVEL SECURITY")
        cur.execute("CREATE INDEX IF NOT EXISTS chat_log_created ON chat_log (created_at)")
        if have and have[0] == want and not force:
            pg.commit()
            return False
        for table in RETIRED_TABLES:  # data that must no longer be served, e.g. the draft BMS spread
            cur.execute(f"DROP TABLE IF EXISTS {table}")
        lite = sqlite3.connect(bundled)
        for table, cols in TABLES.items():
            types = {r[1]: r[2] for r in lite.execute(f"PRAGMA table_info({table})")}
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.execute(f"CREATE TABLE {table} ({', '.join(f'{_q(c)} {PG_TYPES[types[c]]}' for c in cols)})")
            rows = lite.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            with cur.copy(f"COPY {table} ({', '.join(_q(c) for c in cols)}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
            cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")  # the public REST API can read nothing
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            if cur.fetchone()[0] != len(rows):
                raise RuntimeError(f"{table}: hosted row count differs from the bundled file")
        cur.execute("INSERT INTO alice_meta VALUES ('data_checksum', %s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                    (want,))
        pg.commit()
        lite.close()
    _state["seeded_at"] = time.time()
    return True


def sync(bundled: Path) -> Path:
    """Copy the hosted tables into the local cache; returns the SQLite file the lookups should read."""
    if not DATABASE_URL:
        return bundled
    try:
        with _lock:
            if _state["synced_at"] is None:  # first sync of this instance: make sure Supabase is set up
                seed(bundled)
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


def chat_log_count() -> int | None:
    """How many chats are stored (a count only: the content is never served)."""
    if not DATABASE_URL:
        return None
    try:
        with connect().cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM chat_log")
            return cur.fetchone()[0]
    except Exception:  # noqa: BLE001
        return None


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
