import json
import os
import random
import signal
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from twilio.rest import Client

from database import init_db, get_conn
from praamid import (
    ROUTES,
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

CHECK_TIMEOUT = int(
    os.environ.get(
        "TRACKER_CHECK_TIMEOUT_SECONDS",
        "90",
    )
)

# After this many completed cycles, restart cleanly even if everything
# appears healthy. With 2–3 minute cadence, 240 cycles is roughly 8–12h.
# Railway Restart Policy=Always will start a fresh container.
MAX_CYCLES_BEFORE_REFRESH = int(
    os.environ.get(
        "MAX_CYCLES_BEFORE_REFRESH",
        "240",
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


RESOURCE_EXHAUSTION_MARKERS = (
    "Resource temporarily unavailable",
    "can't start new thread",
    "Cannot allocate memory",
    "Too many open files",
)


def fatal_resource_error(text):
    value = str(text)

    return any(
        marker in value
        for marker
        in RESOURCE_EXHAUSTION_MARKERS
    )


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


def kill_process_group(
    proc,
):
    if proc.poll() is not None:
        return

    try:
        os.killpg(
            proc.pid,
            signal.SIGTERM,
        )

    except ProcessLookupError:
        return

    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass

    try:
        proc.wait(
            timeout=5
        )
        return

    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(
            proc.pid,
            signal.SIGKILL,
        )

    except ProcessLookupError:
        return

    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    try:
        proc.wait(
            timeout=5
        )
    except Exception:
        pass


def check_with_subprocess(t):
    """
    Runs one Praamid check in a disposable OS subprocess.

    Important:
    - no Python multiprocessing.Queue
    - no feeder threads
    - process is started in its own process group
    - if it hangs, the whole group is killed, including Chromium descendants
    """
    payload = {
        "direction": t["direction"],
        "travel_date": (
            t["travel_date"]
            .isoformat()
        ),
        "departure_time": (
            t["departure_time"]
            .strftime("%H:%M")
        ),
        "vehicle_type": t[
            "vehicle_type"
        ],
    }

    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "check_once.py",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            bufsize=1,
        )

    except Exception as error:
        if fatal_resource_error(
            error
        ):
            print(
                "  💥 Resource exhaustion while "
                "starting check subprocess.",
                flush=True,
            )

            raise SystemExit(75)

        raise

    try:
        stdout, stderr = (
            proc.communicate(
                input=json.dumps(
                    payload
                ),
                timeout=CHECK_TIMEOUT,
            )
        )

    except subprocess.TimeoutExpired:
        print(
            f"  ⏱ Check exceeded "
            f"{CHECK_TIMEOUT}s. "
            "Killing whole check process group...",
            flush=True,
        )

        kill_process_group(
            proc
        )

        raise TimeoutError(
            "Praamid availability check "
            f"timed out after "
            f"{CHECK_TIMEOUT} seconds"
        )

    # The check should have completely exited by now.
    # Kill any unexpected survivors in the same process group.
    if proc.poll() is None:
        kill_process_group(
            proc
        )

    stdout = (
        stdout or ""
    ).strip()

    stderr = (
        stderr or ""
    ).strip()

    if fatal_resource_error(
        stdout + "\n" + stderr
    ):
        print(
            "  💥 Resource exhaustion detected. "
            "Exiting worker so Railway can "
            "start a clean container.",
            flush=True,
        )

        raise SystemExit(75)

    result = None

    # Only the last non-empty JSON line matters.
    for line in reversed(
        [
            line.strip()
            for line
            in stdout.splitlines()
            if line.strip()
        ]
    ):
        try:
            result = json.loads(
                line
            )
            break

        except json.JSONDecodeError:
            continue

    if (
        proc.returncode != 0
        or not result
        or not result.get("ok")
    ):
        detail = (
            result.get("error")
            if isinstance(
                result,
                dict,
            )
            else None
        )

        if not detail:
            detail = (
                stderr[-1200:]
                or stdout[-1200:]
                or (
                    "Check subprocess exited "
                    f"with code "
                    f"{proc.returncode}"
                )
            )

        raise RuntimeError(
            detail
        )

    attempts_used = result.get(
        "attempts_used",
        1,
    )

    if attempts_used > 1:
        print(
            f"  ↻ Check succeeded on retry "
            f"{attempts_used}.",
            flush=True,
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
        check_with_subprocess(t)
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
        result_text = (
            "availability not explicitly "
            "confirmed"
        )

    elif count == 0:
        result_text = "0 available"

    else:
        result_text = (
            f"{count} available"
        )

    print(
        f"  Result: "
        f"{result_text}",
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

        except SystemExit:
            raise

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

            if fatal_resource_error(
                error
            ):
                print(
                    "  💥 Fatal resource exhaustion. "
                    "Exiting worker for clean restart.",
                    flush=True,
                )

                raise SystemExit(
                    75
                )

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
        f"Per-tracker hard timeout: "
        f"{CHECK_TIMEOUT} seconds",
        flush=True,
    )

    print(
        "Process-group watchdog: enabled",
        flush=True,
    )

    print(
        f"Preventive clean restart after "
        f"{MAX_CYCLES_BEFORE_REFRESH} cycles",
        flush=True,
    )

    completed_cycles = 0

    while True:

        try:
            run_cycle()

        except SystemExit:
            raise

        except Exception as error:
            print(
                f"❌ Unexpected cycle error: "
                f"{type(error).__name__}: "
                f"{error}",
                flush=True,
            )

            if fatal_resource_error(
                error
            ):
                print(
                    "💥 Fatal resource exhaustion. "
                    "Exiting for Railway restart.",
                    flush=True,
                )

                raise SystemExit(
                    75
                )

        completed_cycles += 1

        if (
            MAX_CYCLES_BEFORE_REFRESH > 0
            and completed_cycles
            >= MAX_CYCLES_BEFORE_REFRESH
        ):
            print(
                "♻️ Preventive worker refresh: "
                "cycle limit reached. "
                "Exiting cleanly so Railway "
                "restarts the container.",
                flush=True,
            )

            raise SystemExit(
                0
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
