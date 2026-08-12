import asyncio
import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


# =========================================================
# SETTINGS
# =========================================================

TARGET_DATE = date(2026, 8, 19)
TARGET_TIME = "19:00"
TARGET_ROUTE = "Rohuküla → Heltermaa"

PRAAMID_URL = (
    "https://www.praamid.ee/portal/ticket/departure?direction=RH"
)

CHECK_INTERVAL_SECONDS = 180

# Keep True for the first test.
# It will send a Telegram message after every successful check.
# Once confirmed working, we will change this to False.
TEST_MODE = True

TZ = ZoneInfo("Europe/Tallinn")


# =========================================================
# TELEGRAM
# =========================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


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

async def get_page_text(page):
    return await page.locator("body").inner_text()


async def dismiss_cookies(page):
    possible_texts = [
        "Sain aru",
        "Nõustun",
        "Nõustu",
        "Accept",
        "Accept all",
        "Luba kõik",
    ]

    for text in possible_texts:
        try:
            locator = page.get_by_text(
                text,
                exact=True,
            )

            if await locator.count():
                await locator.first.click(
                    timeout=2000
                )

                await page.wait_for_timeout(500)

                print(
                    f"Cookie button clicked: {text}",
                    flush=True,
                )

                return

        except Exception:
            pass


# =========================================================
# SELECT 19 AUGUST
# =========================================================

async def select_target_date(page):
    print(
        "Opening date picker...",
        flush=True,
    )

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

    await page.wait_for_timeout(500)

    print(
        "Date picker opened.",
        flush=True,
    )

    # We discovered from the actual Praamid DOM that
    # Flatpickr gives each day an aria-label such as:
    #
    # "Kolmapäev, 12. August, 2026, Valitud"
    #
    # Therefore we can directly select 19 August.

    target_day = page.locator(
        '.flatpickr-day[aria-label*="19. August, 2026"]'
    )

    count = await target_day.count()

    print(
        "19 August calendar matches:",
        count,
        flush=True,
    )

    if count == 0:
        raise RuntimeError(
            "Could not find 19 August 2026 in the date picker."
        )

    # Pick a non-disabled 19 August day.
    chosen = None

    for i in range(count):
        candidate = target_day.nth(i)

        classes = (
            await candidate.get_attribute("class")
            or ""
        )

        if "flatpickr-disabled" not in classes:
            chosen = candidate
            break

    if chosen is None:
        raise RuntimeError(
            "19 August exists in the calendar but is disabled."
        )

    await chosen.click(
        timeout=5000
    )

    print(
        "Clicked 19 August.",
        flush=True,
    )

    # Give Angular time to reload the departures.
    await page.wait_for_timeout(2500)

    # Verify selected date from hidden input.
    try:
        selected_value = await page.locator(
            "#departureDate"
        ).get_attribute("value")

        print(
            "Selected departureDate:",
            selected_value,
            flush=True,
        )

        if selected_value and "2026-08-19" not in selected_value:
            raise RuntimeError(
                f"Wrong date selected: {selected_value}"
            )

    except Exception as error:
        print(
            "Date verification warning:",
            repr(error),
            flush=True,
        )


# =========================================================
# FIND 19:00 DEPARTURE
# =========================================================

async def find_departure_block(page):
    body_text = await get_page_text(page)

    if TARGET_TIME not in body_text:
        raise RuntimeError(
            "19:00 departure was not found on 19 August."
        )

    time_matches = page.get_by_text(
        re.compile(
            rf"^{re.escape(TARGET_TIME)}$"
        )
    )

    count = await time_matches.count()

    print(
        "Exact 19:00 matches:",
        count,
        flush=True,
    )

    # If exact matching doesn't work, use a broader search.
    if count == 0:
        time_matches = page.get_by_text(
            re.compile(
                rf"\b{re.escape(TARGET_TIME)}\b"
            )
        )

        count = await time_matches.count()

    if count == 0:
        raise RuntimeError(
            "Could not locate the 19:00 departure element."
        )

    for i in range(min(count, 20)):
        element = time_matches.nth(i)

        try:
            current = element

            # Walk up through parents until we find the
            # complete departure card.
            for _ in range(14):
                text = (
                    await current.inner_text(
                        timeout=1500
                    )
                ).strip()

                lower = text.lower()

                if (
                    TARGET_TIME in text
                    and len(text) < 5000
                    and (
                        "sõiduauto" in lower
                        or "e-pilet" in lower
                        or "vali pilet" in lower
                        or "välja müüdud" in lower
                        or "saadaval" in lower
                    )
                ):
                    print(
                        "Departure card found:",
                        " ".join(text.split())[:1500],
                        flush=True,
                    )

                    return text

                current = current.locator("..")

        except Exception:
            pass

    # Diagnostic fallback.
    position = body_text.find(
        TARGET_TIME
    )

    if position >= 0:
        context = body_text[
            max(0, position - 700):
            min(len(body_text), position + 1800)
        ]

        send_telegram(
            "⚠️ Found 19:00 but couldn't identify "
            "the complete departure card.\n\n"
            + context
        )

    raise RuntimeError(
        "19:00 exists but its departure card "
        "could not be identified."
    )


# =========================================================
# PASSENGER-CAR AVAILABILITY
# =========================================================

def analyse_car_availability(text):
    clean = " ".join(
        text.split()
    )

    print(
        "Analysing:",
        clean[:2000],
        flush=True,
    )

    # Most useful expected format:
    # Sõiduauto: 4
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
                clean,
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
        phrase in lower
        for phrase in sold_out_terms
    ):
        return (
            False,
            0,
            clean,
        )

    # We found the sailing but couldn't confidently
    # determine the car count.
    return (
        False,
        None,
        clean,
    )


# =========================================================
# ONE COMPLETE CHECK
# =========================================================

async def check_once(page):
    print(
        "\n====================================",
        flush=True,
    )

    print(
        datetime.now(TZ).strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
        "- checking Praamid",
        flush=True,
    )

    await page.goto(
        PRAAMID_URL,
        wait_until="domcontentloaded",
        timeout=45000,
    )

    await page.wait_for_timeout(
        2000
    )

    await dismiss_cookies(page)

    print(
        "Selecting 19 August...",
        flush=True,
    )

    await select_target_date(page)

    print(
        "Searching for 19:00...",
        flush=True,
    )

    departure_text = await find_departure_block(
        page
    )

    available, count, raw = (
        analyse_car_availability(
            departure_text
        )
    )

    print(
        "FINAL RESULT:",
        "available =", available,
        "count =", count,
        flush=True,
    )

    return (
        available,
        count,
        raw,
    )


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
        "✅ Praamid monitor FINAL TEST started!\n\n"
        "Rohuküla → Heltermaa\n"
        "19.08.2026 at 19:00\n"
        "Standard passenger car"
    )

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

        already_alerted = False

        while True:
            try:
                # Stop after target date.
                if (
                    datetime.now(TZ).date()
                    > TARGET_DATE
                ):
                    send_telegram(
                        "Praamid monitor stopped because "
                        "19 August has passed."
                    )
                    break

                available, count, raw = (
                    await check_once(page)
                )

                # FOR TESTING ONLY
                if TEST_MODE:
                    send_telegram(
                        "🔎 CHECK WORKED\n\n"
                        f"{TARGET_ROUTE}\n"
                        "19.08.2026 at 19:00\n"
                        f"Sõiduauto result: {count}\n\n"
                        "Detected departure data:\n"
                        + raw[:1000]
                    )

                # ACTUAL ALERT
                if available:
                    if not already_alerted:
                        send_telegram(
                            "🚨🚨🚨 PRAAMIPILET AVAILABLE! "
                            "🚨🚨🚨\n\n"
                            "Rohuküla → Heltermaa\n"
                            "19.08.2026 at 19:00\n"
                            f"Sõiduauto available: {count}\n\n"
                            "BUY IMMEDIATELY:\n"
                            + PRAAMID_URL
                        )

                        already_alerted = True

                else:
                    # Allows another alert if availability
                    # disappears and later reappears.
                    already_alerted = False

            except Exception as error:
                print(
                    "CHECK ERROR:",
                    repr(error),
                    flush=True,
                )

                try:
                    send_telegram(
                        "⚠️ PRAAMID CHECK ERROR\n\n"
                        f"{type(error).__name__}\n"
                        f"{str(error)[:1500]}"
                    )

                except Exception:
                    pass

            await asyncio.sleep(
                CHECK_INTERVAL_SECONDS
            )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())