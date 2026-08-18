import os
import random
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from twilio.rest import Client

from database import init_db, get_conn
from praamid import ROUTES, check_vehicle_availability, BASE


TZ = ZoneInfo("Europe/Tallinn")

MIN_WAIT = int(
    os.environ.get(
        "CHECK_MIN_SECONDS",
        "60",
    )
)

MAX_WAIT = int(
    os.environ.get(
        "CHECK_MAX_SECONDS",
        "90",
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


def send_sms(
    tracker,
    count,
):
    route = ROUTES[
        tracker["direction"]
    ]

    travel_date = (
        tracker["travel_date"]
        .strftime("%d.%m.%Y")
    )

    departure_time = (
        tracker["departure_time"]
        .strftime("%H:%M")
    )

    if count is None:
        availability_text = (
            "Passenger-car ticket available"
        )

    elif count == 1:
        availability_text = (
            "Passenger car: 1 available"
        )

    else:
        availability_text = (
            f"Passenger cars: {count} available"
        )

    body = (
        "⛴️ Ferry ticket available!\n"
        f"{route}\n"
        f"{travel_date} at {departure_time}\n"
        f"{availability_text}\n"
        f"Buy now: "
        f"{BASE}?direction={tracker['direction']}"
    )

    twilio.messages.create(
        to=tracker["phone_number"],
        messaging_service_sid=(
            MESSAGING_SID
        ),
        body=body,
    )


def save_check_result(
    tracker_id,
    available,
    count,
):
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


def mark_error(
    tracker_id,
    message,
):
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


def tracker_label(
    tracker,
):
    route = ROUTES.get(
        tracker["direction"],
        tracker["direction"],
    )

    travel_date = (
        tracker["travel_date"]
        .strftime("%d.%m.%Y")
    )

    departure_time = (
        tracker["departure_time"]
        .strftime("%H:%M")
    )

    return (
        f"#{tracker['id']} | "
        f"{route} | "
        f"{travel_date} | "
        f"{departure_time} | "
        f"{tracker['vehicle_type']}"
    )


def check_tracker(
    tracker,
):
    label = tracker_label(
        tracker
    )

    print(
        f"→ Checking {label}",
        flush=True,
    )

    target_time = (
        tracker["departure_time"]
        .strftime("%H:%M")
    )

    available, count = (
        check_vehicle_availability(
            tracker["direction"],
            tracker["travel_date"],
            target_time,
            tracker["vehicle_type"],
        )
    )

    save_check_result(
        tracker["id"],
        available,
        count,
    )

    if count is None:
        result_text = (
            "availability not explicitly confirmed"
        )

    elif count == 0:
        result_text = (
            "0 available"
        )

    elif count == 1:
        result_text = (
            "1 available"
        )

    else:
        result_text = (
            f"{count} available"
        )

    print(
        f"  Result: {result_text}",
        flush=True,
    )

    with get_conn() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT alert_sent
                FROM trackers
                WHERE id = %s
                FOR UPDATE
                """,
                (
                    tracker["id"],
                ),
            )

            row = cur.fetchone()

            if not row:
                print(
                    "  Tracker disappeared before "
                    "result could be processed.",
                    flush=True,
                )
                return

            alerted_for_current_opening = (
                row["alert_sent"]
            )

            # New availability event:
            # send exactly one alert.
            if (
                available
                and not alerted_for_current_opening
            ):
                cur.execute(
                    """
                    UPDATE trackers
                    SET
                        alert_sent = TRUE,
                        alert_sent_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        tracker["id"],
                    ),
                )

                should_send = True

                print(
                    "  New availability event detected.",
                    flush=True,
                )

            # Availability was previously open,
            # but has now disappeared.
            # Re-arm the tracker for the next opening.
            elif (
                not available
                and alerted_for_current_opening
            ):
                cur.execute(
                    """
                    UPDATE trackers
                    SET
                        alert_sent = FALSE,
                        alert_sent_at = NULL
                    WHERE id = %s
                    """,
                    (
                        tracker["id"],
                    ),
                )

                should_send = False

                print(
                    "  Availability disappeared. "
                    "Tracker re-armed.",
                    flush=True,
                )

            else:
                should_send = False

                if (
                    available
                    and alerted_for_current_opening
                ):
                    print(
                        "  Same availability event is "
                        "still open. No repeat SMS.",
                        flush=True,
                    )

                else:
                    print(
                        "  No availability. "
                        "Continuing to monitor.",
                        flush=True,
                    )

    if should_send:
        try:
            send_sms(
                tracker,
                count,
            )

            print(
                "  ✅ SMS alert sent.",
                flush=True,
            )

        except Exception as error:

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
                            f"SMS failed: "
                            f"{str(error)[:700]}",
                            tracker["id"],
                        ),
                    )

            print(
                f"  ❌ SMS failed: {error}",
                flush=True,
            )

            raise


def run_cycle():
    cycle_started = (
        datetime.now(TZ)
        .strftime("%d.%m.%Y %H:%M:%S")
    )

    expire_old()

    trackers = get_active()

    print(
        "",
        flush=True,
    )

    print(
        "=" * 72,
        flush=True,
    )

    print(
        f"Praamid check cycle started: "
        f"{cycle_started}",
        flush=True,
    )

    print(
        f"Active trackers: "
        f"{len(trackers)}",
        flush=True,
    )

    if not trackers:
        print(
            "No active trackers to check.",
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

    while True:
        run_cycle()

        wait = random.randint(
            MIN_WAIT,
            MAX_WAIT,
        )

        print(
            f"Next cycle in "
            f"{wait} seconds "
            f"({wait / 60:.1f} min)",
            flush=True,
        )

        time.sleep(
            wait
        )


if __name__ == "__main__":
    main()
