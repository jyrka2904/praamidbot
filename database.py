import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]


@contextmanager
def get_conn():
    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        autocommit=False,
    )
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
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id BIGSERIAL PRIMARY KEY,
                    phone_number TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    phone_verified BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
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
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (
                        user_id,
                        direction,
                        travel_date,
                        departure_time,
                        vehicle_type
                    )
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id BIGSERIAL PRIMARY KEY,
                    tracker_id BIGINT NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
                    availability_count INTEGER,
                    channel TEXT NOT NULL DEFAULT 'sms',
                    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_trackers_open
                ON trackers(user_id, status, travel_date, departure_time)
                """
            )
