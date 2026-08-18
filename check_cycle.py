import json
import sys
import time
from datetime import date

from praamid import PraamidBrowserSession


def log(message):
    print(message, file=sys.stderr, flush=True)


def run_one(session, tracker):
    return session.check_vehicle_availability(
        tracker["direction"],
        date.fromisoformat(tracker["travel_date"]),
        tracker["departure_time"],
        tracker.get("vehicle_type") or "Sõiduauto",
    )


def main():
    payload = json.loads(sys.stdin.read())
    trackers = payload.get("trackers") or []
    results = []

    try:
        log(f"🌐 Starting Chromium for {len(trackers)} tracker(s)")

        with PraamidBrowserSession() as session:
            for index, tracker in enumerate(trackers, start=1):
                tid = tracker["id"]
                started = time.monotonic()
                log(
                    f"  → Browser check {index}/{len(trackers)} | tracker #{tid} "
                    f"| {tracker['direction']} | {tracker['travel_date']} "
                    f"| {tracker['departure_time']}"
                )
                last_error = None

                for attempt in (1, 2):
                    try:
                        available, count = run_one(session, tracker)
                        elapsed = time.monotonic() - started
                        suffix = " after clean-browser retry" if attempt == 2 else ""
                        log(f"    ✓ tracker #{tid} completed in {elapsed:.1f}s{suffix}")
                        results.append({
                            "id": tid, "available": available,
                            "count": count, "error": None,
                        })
                        last_error = None
                        break
                    except Exception as error:
                        last_error = f"{type(error).__name__}: {error}"
                        log(f"    ✕ tracker #{tid} attempt {attempt}/2 failed: {last_error}")
                        if attempt == 1:
                            log("    ♻️ Rebuilding Chromium before retry...")
                            try:
                                session.restart()
                            except Exception as restart_error:
                                last_error += (
                                    "; browser restart failed: "
                                    f"{type(restart_error).__name__}: {restart_error}"
                                )
                                break

                if last_error is not None:
                    results.append({
                        "id": tid, "available": False,
                        "count": None, "error": last_error,
                    })
                    log("    ♻️ Preparing clean Chromium for next tracker...")
                    session.restart()

        log("🌐 Chromium cycle closed cleanly")
        print(json.dumps({"ok": True, "results": results}, ensure_ascii=False), flush=True)

    except BaseException as error:
        log(f"💥 Browser cycle failed: {type(error).__name__}: {error}")
        print(json.dumps({
            "ok": False, "error_type": type(error).__name__,
            "error": str(error), "results": results,
        }, ensure_ascii=False), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
