import os
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from twilio.rest import Client

from database import init_db, get_conn
from praamid import (
    ROUTES,
    check_vehicle_availability,
    BASE,
)


TZ = ZoneInfo("Europe/Tallinn")

MIN_WAIT = int(
    os.environ.get(
        "CHECK_MIN_SECONDS",
        "120",
    )
)

MAX_WAIT = int(
    os.environ.get(
        "CHECK_MAX_SECONDS",
        "180",
    )
)

twilio = Client(
    os.environ["TWILIO_ACCOUNT_SID"],
    os.environ["TWILIO_AUTH_TOKEN"],
)

MESSAGING_SID = os.environ[
    "TWILIO_MESSAGING_SERVICE_SID"
]

init_db()


def expire_old():
    now = datetime.now(TZ)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM trackers
                WHERE travel_date < %s
                   OR (
                        travel_date=%s
                        AND departure_time <= %s
                   )
            """, (
                now.date(),
                now.date(),
                now.time().replace(tzinfo=None),
            ))


def get_active():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Language is read from the user so the SMS follows
            # the language selected in the web app.
            cur.execute("""
                SELECT
                    t.*,
                    u.phone_number,
                    COALESCE(u.language,'et') AS language
                FROM trackers t
                JOIN users u
                    ON u.id=t.user_id
                WHERE t.status='active'
                ORDER BY
                    t.travel_date,
                    t.departure_time
            """)

            return cur.fetchall()


def tracker_label(t):
    return (
        f"#{t['id']} | "
        f"{ROUTES.get(t['direction'], t['direction'])} | "
        f"{t['travel_date'].strftime('%d.%m.%Y')} | "
        f"{t['departure_time'].strftime('%H:%M')} | "
        f"{t['vehicle_type']}"
    )


def build_sms(t, count):
    route = ROUTES[t["direction"]]
    d = t["travel_date"].strftime(
        "%d.%m.%Y"
    )
    dep = t["departure_time"].strftime(
        "%H:%M"
    )

    lang = (
        t.get("language")
        if t.get("language") in {"et", "en"}
        else "et"
    )

    if lang == "et":

        if count is None:
            availability = (
                "Sõiduauto pilet on saadaval."
            )
        elif count == 1:
            availability = (
                "1 sõiduauto pilet on saadaval."
            )
        else:
            availability = (
                f"{count} sõiduauto piletit on saadaval."
            )

        return (
            "⛴️ Praamipilet on saadaval!\n"
            f"{route}\n"
            f"{d} kell {dep}\n"
            f"{availability}\n\n"
            "Selle väljumise jälgija eemaldati nüüd automaatselt. "
            "Kui sul ei õnnestu piletit saada, lisa sama jälgija "
            "Praamid.ee Trackeris uuesti.\n\n"
            f"Osta pilet: {BASE}?direction={t['direction']}"
        )

    if count is None:
        availability = (
            "Passenger-car ticket is available."
        )
    elif count == 1:
        availability = (
            "1 passenger-car ticket available."
        )
    else:
        availability = (
            f"{count} passenger-car tickets available."
        )

    return (
        "⛴️ Ferry ticket available!\n"
        f"{route}\n"
        f"{d} at {dep}\n"
        f"{availability}\n\n"
        "This tracker has now been removed. "
        "If you don't manage to get the ticket, add the tracker "
        "again in Praamid.ee Tracker.\n\n"
        f"Buy now: {BASE}?direction={t['direction']}"
    )


def update_result(
    tid,
    available,
    count,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE trackers
                SET
                    last_available=%s,
                    last_count=%s,
                    last_checked_at=NOW(),
                    last_error=NULL
                WHERE id=%s
            """, (
                available,
                count,
                tid,
            ))


def mark_error(
    tid,
    error,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE trackers
                SET
                    last_checked_at=NOW(),
                    last_error=%s
                WHERE id=%s
            """, (
                str(error)[:900],
                tid,
            ))


def send_and_remove(
    t,
    count,
):
    # If Twilio rejects the request, the tracker stays active.
    msg = twilio.messages.create(
        to=t["phone_number"],
        messaging_service_sid=(
            MESSAGING_SID
        ),
        body=build_sms(
            t,
            count,
        ),
    )

    print(
        f"  📲 SMS submitted to Twilio "
        f"(SID {msg.sid}, language={t['language']}).",
        flush=True,
    )

    # Remove only this exact tracker after Twilio accepts the request.
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM trackers
                WHERE id=%s
                """,
                (
                    t["id"],
                ),
            )

    print(
        "  🗑 Tracker removed after availability alert.",
        flush=True,
    )


def check_tracker(t):
    print(
        f"→ Checking {tracker_label(t)}",
        flush=True,
    )

    dep = t["departure_time"].strftime(
        "%H:%M"
    )

    available, count = (
        check_vehicle_availability(
            t["direction"],
            t["travel_date"],
            dep,
            t["vehicle_type"],
        )
    )

    update_result(
        t["id"],
        available,
        count,
    )

    if count is None:
        result = (
            "availability not explicitly confirmed"
        )
    elif count == 0:
        result = "0 available"
    else:
        result = f"{count} available"

    print(
        f"  Result: {result}",
        flush=True,
    )

    if available:
        print(
            "  🎟 Availability detected.",
            flush=True,
        )

        send_and_remove(
            t,
            count,
        )

    else:
        print(
            "  No availability. Continuing to monitor.",
            flush=True,
        )


def run_cycle():
    expire_old()
    trackers = get_active()

    print("", flush=True)
    print("=" * 72, flush=True)

    print(
        "Praamid check cycle started: "
        + datetime.now(TZ).strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
        flush=True,
    )

    print(
        f"Active trackers: {len(trackers)}",
        flush=True,
    )

    for tracker in trackers:
        try:
            check_tracker(
                tracker
            )
        except Exception as error:
            print(
                f"  ❌ Tracker "
                f"#{tracker['id']} error: "
                f"{error}",
                flush=True,
            )

            mark_error(
                tracker["id"],
                error,
            )

    print(
        "Check cycle finished.",
        flush=True,
    )

    print(
        "=" * 72,
        flush=True,
    )


def main():
    print(
        "Praamid worker started",
        flush=True,
    )

    print(
        f"Check interval: "
        f"{MIN_WAIT}–{MAX_WAIT} seconds",
        flush=True,
    )

    while True:
        run_cycle()

        wait = random.randint(
            MIN_WAIT,
            MAX_WAIT,
        )

        print(
            f"Next cycle in "
            f"{wait}s "
            f"({wait/60:.1f} min)",
            flush=True,
        )

        time.sleep(
            wait
        )


if __name__ == "__main__":
    main()
