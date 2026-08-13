import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
from twilio.rest import Client

from database import get_conn, init_db


TZ = ZoneInfo("Europe/Tallinn")

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_MESSAGING_SERVICE_SID = os.environ[
    "TWILIO_MESSAGING_SERVICE_SID"
]

CHECK_MIN_SECONDS = int(
    os.environ.get(
        "CHECK_MIN_SECONDS",
        "180",
    )
)

CHECK_MAX_SECONDS = int(
    os.environ.get(
        "CHECK_MAX_SECONDS",
        "240",
    )
)

PRAAMID_BASE = (
    "https://www.praamid.ee/"
    "portal/ticket/departure"
)

ROUTES = {
    "RH": "Rohuküla → Heltermaa",
    "HR": "Heltermaa → Rohuküla",
}

MONTH_NAMES = {
    1: "Jaanuar",
    2: "Veebruar",
    3: "Märts",
    4: "Aprill",
    5: "Mai",
    6: "Juuni",
    7: "Juuli",
    8: "August",
    9: "September",
    10: "Oktoober",
    11: "November",
    12: "Detsember",
}

twilio = Client(
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
)

init_db()


def expire_old_trackers():
    now = datetime.now(TZ)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET status = 'expired'
                WHERE status IN ('active', 'paused')
                  AND (
                    travel_date < %s
                    OR (
                        travel_date = %s
                        AND departure_time <= %s
                    )
                  )
                """,
                (
                    now.date(),
                    now.date(),
                    now.time().replace(tzinfo=None),
                ),
            )


def active_trackers():
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
                    t.direction,
                    t.travel_date,
                    t.departure_time
                """
            )

            return cur.fetchall()


def dismiss_cookies(page):
    for text in [
        "Sain aru",
        "Nõustun",
        "Nõustu",
        "Accept",
        "Accept all",
        "Luba kõik",
    ]:
        try:
            locator = page.get_by_text(
                text,
                exact=True,
            )

            if locator.count():
                locator.first.click(
                    timeout=1500
                )

                page.wait_for_timeout(300)
                return

        except Exception:
            pass


def select_target_date(
    page,
    target_date,
):
    button = page.locator(
        "button.datepicker-button."
        "departure-select-date"
    )

    if not button.count():
        raise RuntimeError(
            "Could not find Praamid date picker."
        )

    button.first.click(
        timeout=5000
    )

    page.wait_for_timeout(350)

    month_name = MONTH_NAMES[
        target_date.month
    ]

    selector = (
        '.flatpickr-day'
        f'[aria-label*="'
        f'{target_date.day}. '
        f'{month_name}, '
        f'{target_date.year}'
        f'"]'
    )

    candidates = page.locator(
        selector
    )

    if not candidates.count():
        raise RuntimeError(
            "Requested date is not visible "
            "in the Praamid calendar."
        )

    chosen = None

    for i in range(
        candidates.count()
    ):
        candidate = candidates.nth(i)

        classes = (
            candidate.get_attribute(
                "class"
            )
            or ""
        )

        if (
            "flatpickr-disabled"
            not in classes
        ):
            chosen = candidate
            break

    if chosen is None:
        raise RuntimeError(
            "Requested date is disabled."
        )

    chosen.click(
        timeout=5000
    )

    page.wait_for_timeout(
        1800
    )


def find_departure_block(
    page,
    target_time,
):
    body = page.locator(
        "body"
    ).inner_text()

    if target_time not in body:
        return None

    matches = page.get_by_text(
        re.compile(
            rf"^{re.escape(target_time)}$"
        )
    )

    if not matches.count():
        matches = page.get_by_text(
            re.compile(
                rf"\b{re.escape(target_time)}\b"
            )
        )

    for i in range(
        min(
            matches.count(),
            20,
        )
    ):
        element = matches.nth(i)

        try:
            current = element

            for _ in range(14):
                text = (
                    current.inner_text(
                        timeout=1200
                    )
                    .strip()
                )

                lower = text.lower()

                if (
                    target_time in text
                    and len(text) < 5000
                    and (
                        "sõiduauto" in lower
                        or "e-pilet" in lower
                        or "vali pilet" in lower
                        or "välja müüdud" in lower
                        or "saadaval" in lower
                    )
                ):
                    return text

                current = (
                    current.locator(
                        ".."
                    )
                )

        except Exception:
            pass

    return None


def analyse_vehicle(
    text,
    vehicle_type,
):
    if not text:
        return False, None

    clean = " ".join(
        text.split()
    )

    patterns = [
        rf"{re.escape(vehicle_type)}"
        r"\s*:?\s*(\d+)",

        rf"{re.escape(vehicle_type)}"
        r".*?(\d+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            clean,
            re.IGNORECASE,
        )

        if match:
            count = int(
                match.group(1)
            )

            return (
                count > 0,
                count,
            )

    lower = clean.lower()

    unavailable_terms = [
        "välja müüdud",
        "e-pileteid ei ole",
        "pole saadaval",
        "ei ole saadaval",
        "sold out",
        "unavailable",
    ]

    if any(
        term in lower
        for term in unavailable_terms
    ):
        return False, 0

    return False, None


def send_availability_sms(
    phone,
    route,
    travel_date,
    departure_time,
    vehicle_type,
    count,
    direction,
):
    url = (
        f"{PRAAMID_BASE}"
        f"?direction={direction}"
    )

    message = (
        "PRAAMIPILET AVAILABLE!\n"
        f"{route}\n"
        f"{travel_date.strftime('%d.%m.%Y')} "
        f"at {departure_time.strftime('%H:%M')}\n"
        f"{vehicle_type}: {count} available\n"
        f"Buy now: {url}"
    )

    twilio.messages.create(
        to=phone,
        messaging_service_sid=(
            TWILIO_MESSAGING_SERVICE_SID
        ),
        body=message,
    )


def process_tracker_result(
    tracker,
    available,
    count,
):
    previous = tracker[
        "last_available"
    ]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET last_available = %s,
                    last_count = %s,
                    last_checked_at = NOW(),
                    last_error = NULL
                WHERE id = %s
                """,
                (
                    available,
                    count,
                    tracker["id"],
                ),
            )

    should_alert = (
        available
        and previous is not True
    )

    if not should_alert:
        return

    route = ROUTES[
        tracker["direction"]
    ]

    send_availability_sms(
        tracker["phone_number"],
        route,
        tracker["travel_date"],
        tracker["departure_time"],
        tracker["vehicle_type"],
        count,
        tracker["direction"],
    )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts
                (
                    tracker_id,
                    availability_count,
                    channel
                )
                VALUES (%s, %s, 'sms')
                """,
                (
                    tracker["id"],
                    count,
                ),
            )


def mark_error(
    tracker_ids,
    error,
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET last_checked_at = NOW(),
                    last_error = %s
                WHERE id = ANY(%s)
                """,
                (
                    str(error)[:1000],
                    tracker_ids,
                ),
            )


def run_cycle(page):
    expire_old_trackers()

    trackers = active_trackers()

    groups = defaultdict(list)

    # All users watching the same route + date
    # share one Praamid page load.
    for tracker in trackers:
        key = (
            tracker["direction"],
            tracker["travel_date"],
        )

        groups[key].append(
            tracker
        )

    for (
        direction,
        travel_date,
    ), group in groups.items():
        url = (
            f"{PRAAMID_BASE}"
            f"?direction={direction}"
        )

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=45000,
            )

            page.wait_for_timeout(
                1500
            )

            dismiss_cookies(
                page
            )

            select_target_date(
                page,
                travel_date,
            )

            departure_cache = {}

            for tracker in group:
                target_time = (
                    tracker[
                        "departure_time"
                    ]
                    .strftime(
                        "%H:%M"
                    )
                )

                if (
                    target_time
                    not in departure_cache
                ):
                    departure_cache[
                        target_time
                    ] = (
                        find_departure_block(
                            page,
                            target_time,
                        )
                    )

                text = departure_cache[
                    target_time
                ]

                available, count = (
                    analyse_vehicle(
                        text,
                        tracker[
                            "vehicle_type"
                        ],
                    )
                )

                process_tracker_result(
                    tracker,
                    available,
                    count,
                )

        except Exception as error:
            print(
                "Group check failed:",
                direction,
                travel_date,
                repr(error),
                flush=True,
            )

            mark_error(
                [
                    tracker["id"]
                    for tracker
                    in group
                ],
                error,
            )


def main():
    print(
        "Praamid worker started.",
        flush=True,
    )

    while True:
        try:
            with sync_playwright() as p:
                browser = (
                    p.chromium.launch(
                        headless=True,
                        args=[
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                        ],
                    )
                )

                page = browser.new_page(
                    locale="et-EE",
                    timezone_id=(
                        "Europe/Tallinn"
                    ),
                    viewport={
                        "width": 1440,
                        "height": 1200,
                    },
                )

                while True:
                    run_cycle(
                        page
                    )

                    wait_seconds = (
                        random.randint(
                            CHECK_MIN_SECONDS,
                            CHECK_MAX_SECONDS,
                        )
                    )

                    print(
                        "Next check in",
                        wait_seconds,
                        "seconds.",
                        flush=True,
                    )

                    time.sleep(
                        wait_seconds
                    )

        except Exception as error:
            print(
                "Worker crashed:",
                repr(error),
                flush=True,
            )

            time.sleep(15)


if __name__ == "__main__":
    main()
