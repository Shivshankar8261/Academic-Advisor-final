"""
Creates the hosted tables in Supabase and loads them from the bundled courses.db (run after extract_data.py and
extract_extra.py). Re-running replaces the data tables; the chat log is created once and never emptied.

    POSTGRES_URL=... python load_supabase.py
"""
import sqlite3

import dbstore
from config import COURSES_DB

PG_TYPES = {"INTEGER": "bigint", "REAL": "double precision", "TEXT": "text"}

CHAT_LOG = """CREATE TABLE IF NOT EXISTS chat_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    strategy text, question text, answer text, provider text, total_tokens integer, cost_usd double precision,
    response_time_s double precision, needs_clarification boolean, insufficient_information boolean, guard text,
    had_profile boolean, sources text)"""


def main():
    lite = sqlite3.connect(COURSES_DB)
    pg = dbstore.connect()
    with pg.cursor() as cur:
        for table, cols in dbstore.TABLES.items():
            types = {r[1]: r[2] for r in lite.execute(f"PRAGMA table_info({table})")}
            cur.execute(f"DROP TABLE IF EXISTS {table}")
            cur.execute(f"CREATE TABLE {table} ({', '.join(f'{dbstore._q(c)} {PG_TYPES[types[c]]}' for c in cols)})")
            rows = lite.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
            with cur.copy(f"COPY {table} ({', '.join(dbstore._q(c) for c in cols)}) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
            cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")  # nothing readable through the public API
            print(f"{table}: {len(rows)} rows")
        cur.execute(CHAT_LOG)
        cur.execute("ALTER TABLE chat_log ENABLE ROW LEVEL SECURITY")
        cur.execute("CREATE INDEX IF NOT EXISTS chat_log_created ON chat_log (created_at)")
        for table in dbstore.TABLES:  # verify: hosted row counts must equal the bundled ones
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            assert cur.fetchone()[0] == lite.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], table
    print("Supabase loaded and verified against courses.db")


if __name__ == "__main__":
    main()
