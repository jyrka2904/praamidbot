import os
import random
import time
import multiprocessing
from datetime import datetime
from zoneinfo import ZoneInfo
from queue import Empty

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

# A single Praamid/Chromium check is never allowed
# to block the whole worker indefinitely.
CHECK_TIMEOUT = int(
    os.environ.get(
        "TRACKER_CHECK_TIMEOUT_SECONDS",
        "70",
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
                now.time().replace(
                    tzinfo=None
                ),
            ))


def get_active():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Language is read from the user so the SMS
            # follows the language selected in the web app.
            cur.execute("""
                SELECT
                    t.*,
                    u.phone_number,
                    COALESCE(
                        u.language,
                        'et'
                    ) AS language
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
    route = ROUTES[
        t["direction"]
    ]

    d = (
        t["travel_date"]
        .strftime("%d.%m.%Y")
    )

    dep = (
        t["departure_time"]
        .strftime("%H:%M")
    )

    lang = (
        t.get("language")
        if t.get("language")
        in {"et", "en"}
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
                f"{count} sõiduauto piletit "
                "on saadaval."
            )

        return (
            "⛴️ Praamipilet on saadaval!\n"
            f"{route}\n"
            f"{d} kell {dep}\n"
            f"{availability}\n\n"
            "Selle väljumise jälgija eemaldati "
            "nüüd automaatselt. "
            "Kui sul ei õnnestu piletit saada, "
            "lisa sama jälgija Praamid.ee "
            "Trackeris uuesti.\n\n"
            f"Osta pilet: "
            f"{BASE}?direction="
            f"{t['direction']}"
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
            f"{count} passenger-car tickets "
            "available."
        )

    return (
        "⛴️ Ferry ticket available!\n"
        f"{route}\n"
        f"{d} at {dep}\n"
        f"{availability}\n\n"
        "This tracker has now been removed. "
        "If you don't manage to get the ticket, "
        "add the tracker again in "
        "Praamid.ee Tracker.\n\n"
        f"Buy now: "
        f"{BASE}?direction="
        f"{t['direction']}"
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
    # If Twilio rejects the request,
    # the tracker stays active.
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
        f"(SID {msg.sid}, "
        f"language={t['language']}).",
        flush=True,
    )

    # Remove only this exact tracker
    # after Twilio accepts the request.
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
        "  🗑 Tracker removed after "
        "availability alert.",
        flush=True,
    )


def _availability_child(
    result_queue,
    direction,
    travel_date,
    departure_time,
    vehicle_type,
):
    """
    Runs inside a separate process.

    Keeping Playwright/Chromium in a child process means
    the parent worker can terminate this process if the
    browser or Praamid.ee ever hangs.
    """
    try:
        available, count = (
            check_vehicle_availability(
                direction,
                travel_date,
                departure_time,
                vehicle_type,
            )
        )

        result_queue.put(
            {
                "ok": True,
                "available": available,
                "count": count,
            }
        )

    except BaseException as error:
        result_queue.put(
            {
                "ok": False,
                "error": (
                    f"{type(error).__name__}: "
                    f"{error}"
                ),
            }
        )


def check_with_watchdog(t):
    """
    Execute one Praamid check with a hard timeout.

    If it hangs:
      - terminate Chromium/Playwright child process
      - return an error
      - continue with the next tracker
    """
    dep = (
        t["departure_time"]
        .strftime("%H:%M")
    )

    # Railway runs Linux containers.
    # fork avoids re-running the whole worker module
    # inside every child process.
    ctx = multiprocessing.get_context(
        "fork"
    )

    result_queue = ctx.Queue(
        maxsize=1
    )

    process = ctx.Process(
        target=_availability_child,
        args=(
            result_queue,
            t["direction"],
            t["travel_date"],
            dep,
            t["vehicle_type"],
        ),
        daemon=True,
    )

    process.start()

    process.join(
        CHECK_TIMEOUT
    )

    if process.is_alive():
        print(
            f"  ⏱ Check exceeded "
            f"{CHECK_TIMEOUT}s. "
            "Terminating stuck browser process...",
            flush=True,
        )

        process.terminate()

        process.join(
            timeout=5
        )

        # If Chromium/child still refuses to die,
        # use SIGKILL as the final fallback.
        if process.is_alive():
            print(
                "  ⚠️ Child did not terminate "
                "cleanly; killing it.",
                flush=True,
            )

            process.kill()

            process.join(
                timeout=5
            )

        # Clean up queue resources.
        result_queue.close()
        result_queue.join_thread()

        raise TimeoutError(
            "Praamid availability check "
            f"timed out after "
            f"{CHECK_TIMEOUT} seconds"
        )

    try:
        result = result_queue.get(
            timeout=3
        )

    except Empty:
        exit_code = (
            process.exitcode
        )

        raise RuntimeError(
            "Praamid check process exited "
            f"without returning a result "
            f"(exit code {exit_code})"
        )

    finally:
        result_queue.close()
        result_queue.join_thread()

    if not result.get("ok"):
        raise RuntimeError(
            result.get(
                "error",
                "Unknown Praamid check error",
            )
        )

    return (
        result["available"],
        result["count"],
    )


def check_tracker(t):
    print(
        f"→ Checking "
        f"{tracker_label(t)}",
        flush=True,
    )

    started = time.monotonic()

    available, count = (
        check_with_watchdog(t)
    )

    elapsed = (
        time.monotonic()
        - started
    )

    print(
        f"  Check completed in "
        f"{elapsed:.1f}s",
        flush=True,
    )

    update_result(
        t["id"],
        available,
        count,
    )

    if count is None:
        result = (
            "availability not explicitly "
            "confirmed"
        )

    elif count == 0:
        result = "0 available"

    else:
        result = (
            f"{count} available"
        )

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
            "  No availability. "
            "Continuing to monitor.",
            flush=True,
        )


def run_cycle():
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
        "Praamid check cycle started: "
        + datetime.now(TZ).strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
        flush=True,
    )

    print(
        f"Active trackers: "
        f"{len(trackers)}",
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

            # Critical reliability behavior:
            # never allow one failed/hung tracker
            # to prevent subsequent trackers
            # from being checked.
            print(
                "  ↪ Continuing with next tracker.",
                flush=True,
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

    print(
        f"Per-tracker watchdog: "
        f"{CHECK_TIMEOUT} seconds",
        flush=True,
    )

    print(
        "Watchdog isolation: enabled "
        "(each Praamid check runs in its own process)",
        flush=True,
    )

    while True:
        try:
            run_cycle()

        except Exception as error:
            # A cycle-level error should not terminate
            # the long-running worker.
            print(
                f"❌ Unexpected cycle error: "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True,
            )

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
