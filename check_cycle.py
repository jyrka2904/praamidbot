import json
import sys
import time
from datetime import date

from praamid import PraamidBrowserSession


def log(message):
    print(message, file=sys.stderr, flush=True)


def main():
    payload = json.loads(sys.stdin.read())
    trackers = payload.get("trackers") or []

    results = []

    try:
        log(
            f"🌐 Starting one shared Chromium browser "
            f"for {len(trackers)} tracker(s)"
        )

        with PraamidBrowserSession() as session:
            for index, tracker in enumerate(trackers, start=1):
                tid = tracker["id"]
                started = time.monotonic()

                log(
                    f"  → Browser check {index}/{len(trackers)} "
                    f"| tracker #{tid} "
                    f"| {tracker['direction']} "
                    f"| {tracker['travel_date']} "
                    f"| {tracker['departure_time']}"
                )

                try:
                    available, count = session.check_vehicle_availability(
                        tracker["direction"],
                        date.fromisoformat(tracker["travel_date"]),
                        tracker["departure_time"],
                        tracker.get("vehicle_type") or "Sõiduauto",
                    )

                    elapsed = time.monotonic() - started

                    log(
                        f"    ✓ tracker #{tid} completed in "
                        f"{elapsed:.1f}s"
                    )

                    results.append(
                        {
                            "id": tid,
                            "available": available,
                            "count": count,
                            "error": None,
                        }
                    )

                except Exception as error:
                    elapsed = time.monotonic() - started

                    log(
                        f"    ✕ tracker #{tid} failed in "
                        f"{elapsed:.1f}s: "
                        f"{type(error).__name__}: {error}"
                    )

                    results.append(
                        {
                            "id": tid,
                            "available": False,
                            "count": None,
                            "error": (
                                f"{type(error).__name__}: {error}"
                            ),
                        }
                    )

        log("🌐 Shared Chromium cycle closed cleanly")

        print(
            json.dumps(
                {
                    "ok": True,
                    "results": results,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    except BaseException as error:
        log(
            f"💥 Shared browser cycle failed: "
            f"{type(error).__name__}: {error}"
        )

        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "results": results,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        sys.exit(1)


if __name__ == "__main__":
    main()
