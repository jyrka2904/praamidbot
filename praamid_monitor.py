import asyncio
import os
import re
import random
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


# =========================================================
# SETTINGS
# =========================================================

TZ = ZoneInfo("Europe/Tallinn")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


# =========================================================
# SAILINGS TO MONITOR
# =========================================================

MONITORS = [
    {
        "route": "Rohuküla → Heltermaa",
        "direction": "RH",
        "date": date(2026, 8, 19),
        "times": ["19:00"],
    },
    {
        "route": "Heltermaa → Rohuküla",
        "direction": "HR",
        "date": date(2026, 8, 23),
        "times": ["14:30", "16:00", "17:30"],
    },
]


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram variables missing", flush=True)
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )

    response.raise_for_status()


# =========================================================
# PAGE HELPERS
# =========================================================

async def dismiss_cookies(page):
    texts = [
        "Sain aru",
        "Nõustun",
        "Nõustu",
        "Accept",
        "Accept all",
        "Luba kõik",
    ]

    for text in texts:
        try:
            locator = page.get_by_text(
                text,
                exact=True,
            )

            if await locator.count():
                await locator.first.click(
                    timeout=2000
                )

                await page.wait_for_timeout(400)
                return

        except Exception:
            pass


# =========================================================
# DATE PICKER
# =========================================================

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


async def select_target_date(page, target_date):
    date_button = page.locator(
        "button.datepicker-button.departure-select-date"
    )

    if not await date_button.count():
        raise RuntimeError(
            "Could not find the departure date picker."
        )

    await date_button.first.click(
        timeout=5000
    )

    await page.wait_for_timeout(400)

    month_name = MONTH_NAMES[
        target_date.month
    ]

    selector = (
        f'.flatpickr-day[aria-label*="'
        f'{target_date.day}. {month_name}, '
        f'{target_date.year}'
        f'"]'
    )

    target_days = page.locator(
        selector
    )

    count = await target_days.count()

    if count == 0:
        raise RuntimeError(
            f"Could not find "
            f"{target_date.strftime('%d.%m.%Y')} "
            "in the calendar."
        )

    chosen = None

    for i in range(count):
        candidate = target_days.nth(i)

        classes = (
            await candidate.get_attribute(
                "class"
            )
            or ""
        )

        if "flatpickr-disabled" not in classes:
            chosen = candidate
            break

    if chosen is None:
        raise RuntimeError(
            f"{target_date.strftime('%d.%m.%Y')} "
            "exists but is disabled."
        )

    await chosen.click(
        timeout=5000
    )

    # Wait for departures to reload
    await page.wait_for_timeout(
        2200
    )


# =========================================================
# FIND DEPARTURE
# =========================================================

async def find_departure_block(
    page,
    target_time,
):
    body = await page.locator(
        "body"
    ).inner_text()

    if target_time not in body:
        return None

    matches = page.get_by_text(
        re.compile(
            rf"^{re.escape(target_time)}$"
        )
    )

    count = await matches.count()

    if count == 0:
        matches = page.get_by_text(
            re.compile(
                rf"\b{re.escape(target_time)}\b"
            )
        )

        count = await matches.count()

    for i in range(
        min(count, 20)
    ):
        element = matches.nth(i)

        try:
            current = element

            for _ in range(14):
                text = (
                    await current.inner_text(
                        timeout=1500
                    )
                ).strip()

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

                current = current.locator(
                    ".."
                )

        except Exception:
            pass

    return None


# =========================================================
# CAR AVAILABILITY
# =========================================================

def analyse_car_availability(
    text,
):
    if not text:
        return False, None

    clean = " ".join(
        text.split()
    )

    patterns = [
        r"Sõiduauto\s*:?\s*(\d+)",
        r"Sõiduauto\s+(\d+)",
        r"Sõiduauto.*?(\d+)",
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

    sold_out_terms = [
        "välja müüdud",
        "e-pileteid ei ole",
        "sõiduauto 0",
        "sõiduauto: 0",
        "pole saadaval",
        "ei ole saadaval",
        "sold out",
        "unavailable",
    ]

    if any(
        term in lower
        for term in sold_out_terms
    ):
        return False, 0

    return False, None


# =========================================================
# CHECK ONE ROUTE / DATE
# =========================================================

async def check_monitor(
    page,
    monitor,
):
    direction = monitor[
        "direction"
    ]

    url = (
        "https://www.praamid.ee/"
        "portal/ticket/departure"
        f"?direction={direction}"
    )

    print(
        f"\nChecking "
        f"{monitor['route']} "
        f"{monitor['date'].strftime('%d.%m.%Y')}",
        flush=True,
    )

    await page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=45000,
    )

    await page.wait_for_timeout(
        1800
    )

    await dismiss_cookies(
        page
    )

    await select_target_date(
        page,
        monitor["date"],
    )

    results = {}

    for target_time in monitor[
        "times"
    ]:
        departure = (
            await find_departure_block(
                page,
                target_time,
            )
        )

        available, count = (
            analyse_car_availability(
                departure
            )
        )

        results[
            target_time
        ] = {
            "available": available,
            "count": count,
        }

        print(
            monitor["route"],
            target_time,
            "Sõiduauto:",
            count,
            flush=True,
        )

    return results


# =========================================================
# MAIN
# =========================================================

async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN missing."
        )

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID missing."
        )

    send_telegram(
        "✅ Praamid monitor is running.\n\n"
        "Monitoring:\n"
        "19.08 Rohuküla → Heltermaa at 19:00\n"
        "23.08 Heltermaa → Rohuküla at "
        "14:30 / 16:00 / 17:30\n\n"
        "Checks happen every 3–4 minutes.\n"
        "I will only message you when a "
        "passenger-car ticket becomes available."
    )

    # Store the previous availability state
    # for each individual sailing.
    previous_states = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = await browser.new_page(
            locale="et-EE",
            timezone_id="Europe/Tallinn",
            viewport={
                "width": 1440,
                "height": 1200,
            },
        )

        while True:
            now = datetime.now(
                TZ
            )

            # Stop automatically after
            # the final monitored date.
            if now.date() > date(
                2026,
                8,
                23,
            ):
                send_telegram(
                    "Praamid monitor stopped — "
                    "all monitored sailings "
                    "have passed."
                )

                break

            for monitor in MONITORS:
                # Don't check a trip
                # after its date has passed.
                if (
                    now.date()
                    > monitor["date"]
                ):
                    continue

                try:
                    results = (
                        await check_monitor(
                            page,
                            monitor,
                        )
                    )

                    for (
                        target_time,
                        result,
                    ) in results.items():
                        key = (
                            monitor["route"],
                            monitor[
                                "date"
                            ].isoformat(),
                            target_time,
                        )

                        available = result[
                            "available"
                        ]

                        count = result[
                            "count"
                        ]

                        previous = (
                            previous_states.get(
                                key,
                                False,
                            )
                        )

                        # Alert only when it changes
                        # from unavailable -> available.
                        if (
                            available
                            and not previous
                        ):
                            send_telegram(
                                "🚨🚨🚨 PRAAMIPILET "
                                "AVAILABLE! 🚨🚨🚨\n\n"
                                f"{monitor['route']}\n"
                                f"{monitor['date'].strftime('%d.%m.%Y')} "
                                f"at {target_time}\n"
                                f"Sõiduauto available: "
                                f"{count}\n\n"
                                "BUY NOW:\n"
                                "https://www.praamid.ee/"
                                "portal/ticket/departure"
                                f"?direction="
                                f"{monitor['direction']}"
                            )

                        previous_states[
                            key
                        ] = available

                except Exception as error:
                    print(
                        "CHECK ERROR:",
                        monitor["route"],
                        repr(error),
                        flush=True,
                    )

            # Random wait between
            # 3 and 4 minutes.
            wait_seconds = (
                random.randint(
                    180,
                    240,
                )
            )

            print(
                f"Next check in "
                f"{wait_seconds} seconds",
                flush=True,
            )

            await asyncio.sleep(
                wait_seconds
            )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())