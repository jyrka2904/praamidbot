import json
import sys
from datetime import date

from praamid import check_vehicle_availability


def main():
    """
    Perform exactly ONE Playwright availability attempt.

    Retries intentionally live in worker.py, which starts a brand-new OS
    subprocess for every attempt. That means a timed-out/broken Playwright
    instance can never contaminate the next retry in the same Python process.
    """
    payload = json.loads(sys.stdin.read())

    travel_date = date.fromisoformat(
        payload["travel_date"]
    )

    try:
        available, count = check_vehicle_availability(
            payload["direction"],
            travel_date,
            payload["departure_time"],
            payload["vehicle_type"],
        )

        print(
            json.dumps(
                {
                    "ok": True,
                    "available": available,
                    "count": count,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    except BaseException as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
