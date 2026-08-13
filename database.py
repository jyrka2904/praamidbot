import os
import psycopg
from psycopg.rows import dict_row
from contextlib import contextmanager

DATABASE_URL = os.environ["DATABASE_URL"]

@contextmanager
def get_conn():
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    phone_number TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    phone_verified BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trackers (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    direction TEXT NOT NULL,
                    travel_date DATE NOT NULL,
                    departure_time TIME NOT NULL,
                    vehicle_type TEXT NOT NULL DEFAULT 'Sõiduauto',
                    status TEXT NOT NULL DEFAULT 'active',
                    last_available BOOLEAN,
                    last_count INTEGER,
                    last_checked_at TIMESTAMPTZ,
                    last_error TEXT,
                    alert_sent BOOLEAN NOT NULL DEFAULT FALSE,
                    alert_sent_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id, direction, travel_date, departure_time, vehicle_type)
                )
            """)
            # Safe migration for an existing DB.
            cur.execute("ALTER TABLE trackers ADD COLUMN IF NOT EXISTS alert_sent BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE trackers ADD COLUMN IF NOT EXISTS alert_sent_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE trackers ADD COLUMN IF NOT EXISTS last_error TEXT")
            cur.execute("ALTER TABLE trackers ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE trackers ADD COLUMN IF NOT EXISTS last_available BOOLEAN")
            cur.execute("ALTER TABLE trackers ADD COLUMN IF NOT EXISTS last_count INTEGER")
