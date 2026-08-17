import json
import os
import random
import sys
import time
from datetime import date

from praamid import check_vehicle_availability


MAX_ATTEMPTS = int(
    os.environ.get(
        "PRAAMID_CHECK_ATTEMPTS",
        "3",
    )
)

RETRY_BASE_DELAY = float(
    os.environ.get(
        "PRAAMID_RETRY_DELAY_SECONDS",
        "2",
    )
)

TRANSIENT_MARKERS = (
    "Timeout",
    "ERR_ABORTED",
    "ERR_CONNECTION",
    "ERR_NETWORK",
    "ERR_TIMED_OUT",
    "search form did not load",
    "date picker",
    "Departure",
    "Target page, context or browser has been closed",
)


def is_transient(error):
    text = (
        f"{type(error).__name__}: {error}"
    )

    return any(
        marker.lower() in text.lower()
        for marker in TRANSIENT_MARKERS
    )


def main():
    payload = json.loads(
        sys.stdin.read()
    )

    travel_date = date.fromisoformat(
        payload["travel_date"]
    )

    last_error = None

    for attempt in range(
        1,
        MAX_ATTEMPTS + 1,
    ):
        try:
            print(
                json.dumps(
                    {
                        "event": "attempt",
                        "attempt": attempt,
                        "max_attempts": MAX_ATTEMPTS,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )

            available, count = (
                check_vehicle_availability(
                    payload["direction"],
                    travel_date,
                    payload["departure_time"],
                    payload["vehicle_type"],
                )
            )

            print(
                json.dumps(
                    {
                        "ok": True,
                        "available": available,
                        "count": count,
                        "attempts_used": attempt,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return

        except BaseException as error:
            last_error = error

            transient = is_transient(
                error
            )

            print(
                json.dumps(
                    {
                        "event": "attempt_failed",
                        "attempt": attempt,
                        "max_attempts": MAX_ATTEMPTS,
                        "transient": transient,
                        "error_type": (
                            type(error).__name__
                        ),
                        "error": str(error),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )

            if (
                attempt >= MAX_ATTEMPTS
                or not transient
            ):
                break

            # Small jitter avoids immediately hitting the site again
            # in exactly the same state.
            delay = (
                RETRY_BASE_DELAY
                * attempt
                + random.uniform(0.2, 0.8)
            )

            time.sleep(delay)

    print(
        json.dumps(
            {
                "ok": False,
                "error_type": (
                    type(last_error).__name__
                    if last_error
                    else "RuntimeError"
                ),
                "error": (
                    str(last_error)
                    if last_error
                    else "Unknown Praamid check failure"
                ),
                "attempts_used": MAX_ATTEMPTS,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    sys.exit(1)


if __name__ == "__main__":
    main()
