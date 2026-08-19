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
from praamid import ROUTES, BASE


TZ = ZoneInfo("Europe/Tallinn")

# Short pause BETWEEN completed full cycles.
MIN_WAIT = int(os.environ.get("CHECK_MIN_SECONDS", "15"))
MAX_WAIT = int(os.environ.get("CHECK_MAX_SECONDS", "30"))

# Hard timeout for the WHOLE shared-browser cycle subprocess.
# A normal 5–6 tracker cycle should finish well below this.
CYCLE_TIMEOUT = int(
    os.environ.get("CYCLE_CHECK_TIMEOUT_SECONDS", "300")
)

# Preventive clean restart. With continuous checking, 600 cycles is
# several hours rather than days. Set 0 to disable.
MAX_CYCLES_BEFORE_REFRESH = int(
    os.environ.get("MAX_CYCLES_BEFORE_REFRESH", "600")
)

twilio = Client(
    os.environ["TWILIO_ACCOUNT_SID"],
    os.environ["TWILIO_AUTH_TOKEN"],
)

MESSAGING_SID = os.environ["TWILIO_MESSAGING_SERVICE_SID"]

init_db()


RESOURCE_EXHAUSTION_MARKERS = (
    "Resource temporarily unavailable",
    "can't start new thread",
    "Cannot allocate memory",
    "Too many open files",
    "Out of memory",
    "ENOMEM",
)


def fatal_resource_error(text):
    value = str(text)
    return any(marker in value for marker in RESOURCE_EXHAUSTION_MARKERS)


def expire_old():
    now = datetime.now(TZ)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM trackers
                WHERE travel_date < %s
                   OR (
                        travel_date=%s
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
                    u.phone_number,
                    COALESCE(u.language, 'et') AS language
                FROM trackers t
                JOIN users u ON u.id=t.user_id
                WHERE t.status='active'
                ORDER BY
                    t.travel_date,
                    t.departure_time
                """
            )
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
    d = t["travel_date"].strftime("%d.%m.%Y")
    dep = t["departure_time"].strftime("%H:%M")

    lang = (
        t.get("language")
        if t.get("language") in {"et", "en"}
        else "et"
    )

    if lang == "et":
        if count is None:
            availability = "Sõiduauto pilet on saadaval."
        elif count == 1:
            availability = "1 sõiduauto pilet on saadaval."
        else:
            availability = f"{count} sõiduauto piletit on saadaval."

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
        availability = "Passenger-car ticket is available."
    elif count == 1:
        availability = "1 passenger-car ticket available."
    else:
        availability = f"{count} passenger-car tickets available."

    return (
        "⛴️ Ferry ticket available!\n"
        f"{route}\n"
        f"{d} at {dep}\n"
        f"{availability}\n\n"
        "This tracker has now been removed. "
        "If you don't manage to get the ticket, add the tracker again "
        "in Praamid.ee Tracker.\n\n"
        f"Buy now: {BASE}?direction={t['direction']}"
    )


def update_result(tid, available, count):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET
                    last_available=%s,
                    last_count=%s,
                    last_checked_at=NOW(),
                    last_error=NULL
                WHERE id=%s
                """,
                (available, count, tid),
            )


def mark_error(tid, error):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET
                    last_checked_at=NOW(),
                    last_error=%s
                WHERE id=%s
                """,
                (str(error)[:900], tid),
            )


def send_and_remove(t, count):
    msg = twilio.messages.create(
        to=t["phone_number"],
        messaging_service_sid=MESSAGING_SID,
        body=build_sms(t, count),
    )

    print(
        f"  📲 SMS submitted to Twilio "
        f"(SID {msg.sid}, language={t['language']}).",
        flush=True,
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM trackers WHERE id=%s",
                (t["id"],),
            )

    print(
        "  🗑 Tracker removed after availability alert.",
        flush=True,
    )


def terminate_process_group(pgid, leader=None):
    """
    Kill the complete process group, including Chromium descendants.

    This deliberately works even if the Python child has already exited:
    Chromium grandchildren can otherwise survive and accumulate.
    """
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        if leader is not None and leader.poll() is None:
            try:
                leader.terminate()
            except Exception:
                pass

    if leader is not None and leader.poll() is None:
        try:
            leader.wait(timeout=3)
        except Exception:
            pass

    # Give descendants a short chance to exit cleanly.
    time.sleep(0.15)

    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:
        if leader is not None and leader.poll() is None:
            try:
                leader.kill()
            except Exception:
                pass


def serialize_tracker(t):
    return {
        "id": t["id"],
        "direction": t["direction"],
        "travel_date": t["travel_date"].isoformat(),
        "departure_time": t["departure_time"].strftime("%H:%M"),
        "vehicle_type": t["vehicle_type"],
    }


def run_shared_browser_check(trackers):
    """
    Start ONE disposable subprocess for the whole cycle.

    Inside that subprocess Playwright/Chromium is launched once, all trackers
    are checked using fresh browser contexts/pages, then Chromium is closed.
    The entire subprocess has one hard watchdog.
    """
    payload = {
        "trackers": [serialize_tracker(t) for t in trackers]
    }

    proc = None

    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", "check_cycle.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # Let progress/error lines appear live in Railway logs.
            stderr=None,
            text=True,
            start_new_session=True,
            bufsize=1,
        )
    except Exception as error:
        if fatal_resource_error(error):
            print(
                "💥 Resource exhaustion while starting cycle subprocess.",
                flush=True,
            )
            raise SystemExit(75)
        raise

    pgid = proc.pid

    try:
        try:
            stdout, _ = proc.communicate(
                input=json.dumps(payload),
                timeout=CYCLE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(
                f"⏱ Full browser cycle exceeded {CYCLE_TIMEOUT}s. "
                "Killing the whole cycle process group...",
                flush=True,
            )
            terminate_process_group(pgid, proc)
            raise TimeoutError(
                f"Praamid full cycle timed out after {CYCLE_TIMEOUT} seconds"
            )

        stdout = (stdout or "").strip()

        # Always clean the entire group after the child exits. This catches
        # any Chromium processes that survived a normal/abnormal Playwright exit.
        terminate_process_group(pgid, proc)

        if fatal_resource_error(stdout):
            print(
                "💥 Resource exhaustion detected. "
                "Exiting worker so Railway starts a clean container.",
                flush=True,
            )
            raise SystemExit(75)

        result = None
        for line in reversed(
            [line.strip() for line in stdout.splitlines() if line.strip()]
        ):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        if not isinstance(result, dict):
            raise RuntimeError(
                "Cycle subprocess did not return valid JSON."
            )

        if not result.get("ok"):
            detail = result.get("error") or (
                f"Cycle subprocess exited with code {proc.returncode}"
            )
            if fatal_resource_error(detail):
                raise SystemExit(75)
            raise RuntimeError(detail)

        return result.get("results", [])

    finally:
        # Idempotent extra cleanup for exceptions between Popen and parsing.
        if proc is not None:
            terminate_process_group(pgid, proc)


def process_tracker_result(tracker, result):
    error = result.get("error")

    if error:
        print(
            f"  ❌ Tracker #{tracker['id']} error: {error}",
            flush=True,
        )
        mark_error(tracker["id"], error)
        print(
            "  ↪ Continuing with next tracker.",
            flush=True,
        )
        return

    available = bool(result.get("available"))
    count = result.get("count")

    update_result(
        tracker["id"],
        available,
        count,
    )

    if count is None:
        result_text = "availability not explicitly confirmed"
    elif count == 0:
        result_text = "0 available"
    else:
        result_text = f"{count} available"

    print(
        f"  Result: {result_text}",
        flush=True,
    )

    if available:
        print(
            "  🎟 Availability detected.",
            flush=True,
        )
        send_and_remove(tracker, count)
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
        + datetime.now(TZ).strftime("%d.%m.%Y %H:%M:%S"),
        flush=True,
    )
    print(
        f"Active trackers: {len(trackers)}",
        flush=True,
    )

    if not trackers:
        print(
            "No active trackers.",
            flush=True,
        )
        print("Check cycle finished.", flush=True)
        print("=" * 72, flush=True)
        return

    started = time.monotonic()

    results = run_shared_browser_check(trackers)

    by_id = {
        int(item["id"]): item
        for item in results
        if isinstance(item, dict) and "id" in item
    }

    for tracker in trackers:
        print(
            f"→ Processing {tracker_label(tracker)}",
            flush=True,
        )

        item = by_id.get(int(tracker["id"]))

        if item is None:
            error = "No result returned for this tracker in the browser cycle."
            mark_error(tracker["id"], error)
            print(
                f"  ❌ Tracker #{tracker['id']} error: {error}",
                flush=True,
            )
            continue

        process_tracker_result(tracker, item)

    elapsed = time.monotonic() - started

    print(
        f"Shared-browser cycle completed in {elapsed:.1f}s",
        flush=True,
    )
    print("Check cycle finished.", flush=True)
    print("=" * 72, flush=True)


def main():
    print("Praamid worker started", flush=True)
    print(
        f"Pause between completed cycles: {MIN_WAIT}–{MAX_WAIT} seconds",
        flush=True,
    )
    print(
        f"Whole-cycle hard timeout: {CYCLE_TIMEOUT} seconds",
        flush=True,
    )
    print(
        "Browser mode: fresh context per tracker + clean Chromium retry on failure",
        flush=True,
    )
    print(
        "Process-group watchdog + descendant cleanup: enabled",
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
                f"{type(error).__name__}: {error}",
                flush=True,
            )

            if fatal_resource_error(error):
                print(
                    "💥 Fatal resource exhaustion. "
                    "Exiting for Railway restart.",
                    flush=True,
                )
                raise SystemExit(75)

        completed_cycles += 1

        if (
            MAX_CYCLES_BEFORE_REFRESH > 0
            and completed_cycles >= MAX_CYCLES_BEFORE_REFRESH
        ):
            print(
                "♻️ Preventive worker refresh: cycle limit reached. "
                "Exiting with restart code so Railway starts a clean container.",
                flush=True,
            )
            # Non-zero so Railway Restart Policy = On Failure also restarts it.
            raise SystemExit(75)

        wait = random.randint(MIN_WAIT, MAX_WAIT)

        print(
            f"Next full cycle in {wait}s",
            flush=True,
        )

        time.sleep(wait)


if __name__ == "__main__":
    main()
