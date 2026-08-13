import os, random, time
from datetime import datetime
from zoneinfo import ZoneInfo
from twilio.rest import Client

from database import init_db, get_conn
from praamid import ROUTES, check_vehicle_availability, BASE

TZ = ZoneInfo("Europe/Tallinn")
MIN_WAIT = int(os.environ.get("CHECK_MIN_SECONDS", "180"))
MAX_WAIT = int(os.environ.get("CHECK_MAX_SECONDS", "240"))

twilio = Client(
    os.environ["TWILIO_ACCOUNT_SID"],
    os.environ["TWILIO_AUTH_TOKEN"],
)
MESSAGING_SID = os.environ["TWILIO_MESSAGING_SERVICE_SID"]

init_db()


def expire_old():
    now = datetime.now(TZ)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM trackers
                WHERE travel_date < %s
                   OR (
                        travel_date = %s
                        AND departure_time <= %s
                   )
                """,
                (
                    now.date(),
                    now.date(),
                    now.time().replace(tzinfo=None),
                ),
            )


def get_active():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    t.*,
                    u.phone_number
                FROM trackers t
                JOIN users u
                    ON u.id = t.user_id
                WHERE t.status = 'active'
                ORDER BY
                    t.travel_date,
                    t.departure_time
                """
            )
            return cur.fetchall()


def send_sms(tracker, count):
    route = ROUTES[tracker["direction"]]
    d = tracker["travel_date"].strftime("%d.%m.%Y")
    t = tracker["departure_time"].strftime("%H:%M")

    if count is None:
        availability_text = "Passenger-car ticket available"
    elif count == 1:
        availability_text = "Passenger car: 1 available"
    else:
        availability_text = f"Passenger cars: {count} available"

    body = (
        "⛴️ Ferry ticket available!\n"
        f"{route}\n"
        f"{d} at {t}\n"
        f"{availability_text}\n"
        f"Buy now: {BASE}?direction={tracker['direction']}"
    )

    twilio.messages.create(
        to=tracker["phone_number"],
        messaging_service_sid=MESSAGING_SID,
        body=body,
    )


def save_check_result(tracker_id, available, count):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET
                    last_available = %s,
                    last_count = %s,
                    last_checked_at = NOW(),
                    last_error = NULL
                WHERE id = %s
                """,
                (
                    available,
                    count,
                    tracker_id,
                ),
            )


def mark_error(tracker_id, message):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET
                    last_checked_at = NOW(),
                    last_error = %s
                WHERE id = %s
                """,
                (
                    str(message)[:900],
                    tracker_id,
                ),
            )


def check_tracker(tracker):
    target_time = tracker["departure_time"].strftime("%H:%M")

    available, count = check_vehicle_availability(
        tracker["direction"],
        tracker["travel_date"],
        target_time,
        tracker["vehicle_type"],
    )

    # Persist the latest state first.
    save_check_result(
        tracker["id"],
        available,
        count,
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Lock the row so two worker processes cannot both send the same alert.
            cur.execute(
                """
                SELECT alert_sent
                FROM trackers
                WHERE id = %s
                FOR UPDATE
                """,
                (tracker["id"],),
            )
            row = cur.fetchone()

            if not row:
                return

            alerted_for_current_opening = row["alert_sent"]

            # NEW OPENING:
            # unavailable -> available
            # Send exactly one SMS and mark this availability event as alerted.
            if available and not alerted_for_current_opening:
                cur.execute(
                    """
                    UPDATE trackers
                    SET
                        alert_sent = TRUE,
                        alert_sent_at = NOW()
                    WHERE id = %s
                    """,
                    (tracker["id"],),
                )
                should_send = True

            # RE-ARM:
            # Once availability disappears again, reset alert_sent to FALSE.
            # The next future opening can then generate another SMS.
            elif not available and alerted_for_current_opening:
                cur.execute(
                    """
                    UPDATE trackers
                    SET
                        alert_sent = FALSE,
                        alert_sent_at = NULL
                    WHERE id = %s
                    """,
                    (tracker["id"],),
                )
                should_send = False

                print(
                    f"Tracker {tracker['id']} re-armed "
                    "after availability disappeared.",
                    flush=True,
                )

            else:
                should_send = False

    if should_send:
        try:
            send_sms(tracker, count)

            print(
                f"SMS sent for new availability event "
                f"on tracker {tracker['id']}.",
                flush=True,
            )

        except Exception as error:
            # If SMS delivery itself fails, allow the next cycle to retry
            # for the same still-open availability event.
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE trackers
                        SET
                            alert_sent = FALSE,
                            alert_sent_at = NULL,
                            last_error = %s
                        WHERE id = %s
                        """,
                        (
                            f"SMS failed: {str(error)[:700]}",
                            tracker["id"],
                        ),
                    )
            raise


def run_cycle():
    expire_old()
    trackers = get_active()

    print(
        f"Checking {len(trackers)} active tracker(s)",
        flush=True,
    )

    for tracker in trackers:
        try:
            check_tracker(tracker)
        except Exception as error:
            print(
                f"Tracker {tracker['id']} error: {error}",
                flush=True,
            )
            mark_error(
                tracker["id"],
                error,
            )


def main():
    print(
        "Praamid worker started",
        flush=True,
    )

    while True:
        run_cycle()

        wait = random.randint(
            MIN_WAIT,
            MAX_WAIT,
        )

        print(
            f"Next cycle in {wait}s",
            flush=True,
        )

        time.sleep(wait)


if __name__ == "__main__":
    main()
