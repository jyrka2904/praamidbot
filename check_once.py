import json
import sys
from datetime import date, datetime

from praamid import check_vehicle_availability


def main():
    payload = json.loads(sys.stdin.read())

    travel_date = date.fromisoformat(
        payload["travel_date"]
    )

    departure_time = payload[
        "departure_time"
    ]

    available, count = (
        check_vehicle_availability(
            payload["direction"],
            travel_date,
            departure_time,
            payload["vehicle_type"],
        )
    )

    result = {
        "ok": True,
        "available": available,
        "count": count,
    }

    print(
        json.dumps(
            result,
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()

    except BaseException as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": (
                        type(error).__name__
                    ),
                    "error": str(error),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        sys.exit(1)
