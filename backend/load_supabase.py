"""
Loads (or reloads) the hosted Supabase tables from the bundled courses.db by hand. The backend also does this by
itself at start-up whenever the hosted data is missing or out of date (see dbstore.seed).

    POSTGRES_URL=... python load_supabase.py
"""
import dbstore
from config import COURSES_DB

if __name__ == "__main__":
    dbstore.seed(COURSES_DB, force=True)
    print("Supabase loaded and verified against courses.db")
