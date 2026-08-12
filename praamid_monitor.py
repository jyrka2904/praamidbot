import asyncio
import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


# =========================
# TARGET
# =========================

TARGET_DATE = date(2026, 8, 19)
TARGET_TIME = "19:00"
TARGET_ROUTE = "Rohuküla → Heltermaa"

PRAAMID_URL = (
    "https://www.praamid.ee/portal/ticket/departure?direction=RH"
)

# Check every 3 minutes
CHECK_INTERVAL_SECONDS = 180

TZ = ZoneInfo("Europe/Tallinn")


# =========================
# TELEGRAM
# =========================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram variables are missing.", flush=True)
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


# =========================
# PRAAMID HELPERS
# =========================

async def dismiss_cookies(page):
    possible_buttons = [
        "Nõustun",
        "Nõustu",
        "Accept",
        "Accept all",
        "Luba kõik",
    ]

    for text in possible_buttons:
        try:
            button = page.get_by_role(
                "button",
                name=re.compile(
                    rf"^{re.escape(text)}$",
                    re.IGNORECASE,
                ),
            )

            if await button.count():
                await button.first.click(timeout=1500)
                return

        except Exception:
            pass


async def find_visible_date(page):
    text = await page.locator("body").inner_text()

    matches = re.findall(
        r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b",
        text,
    )

    dates = []

    for d, m, y in matches:
        try:
            dates.append(
                date(
                    int(y),
                    int(m),
                    int(d),
                )
            )
        except ValueError:
            pass

    if dates:
        return min(
            dates,
            key=lambda d: abs(
                (d - TARGET_DATE).days
            ),
        )

    return None


async def click_next_day(page):
    possibilities = [
        re.compile("Järgmine kuupäev", re.I),
        re.compile("^Järgmine$", re.I),
        re.compile("Next date", re.I),
        re.compile("^Next$", re.I),
    ]

    for pattern in possibilities:
        for role in ["button", "link"]:
            try:
                element = page.get_by_role(
                    role,
                    name=pattern,
                )

                if await element.count():
                    await element.first.click(
                        timeout=2500
                    )

                    await page.wait_for_timeout(600)
                    return True

            except Exception:
                pass

    return False


async def move_to_target_date(page):
    current = await find_visible_date(page)

    if current is None:
        current = datetime.now(TZ).date()

    for _ in range(40):
        if current == TARGET_DATE:
            return

        if current > TARGET_DATE:
            raise RuntimeError(
                "Target date is before displayed date."
            )

        success = await click_next_day(page)

        if not success:
            raise RuntimeError(
                "Could not find the next-day button."
            )

        await page.wait_for_timeout(500)

        new_date = await find_visible_date(page)

        if new_date and new_date != current:
            current = new_date
        else:
            current = date.fromordinal(
                current.toordinal() + 1
            )

    raise RuntimeError(
        "Could not navigate to target date."
    )


async def get_target_departure(page):
    matches = page.get_by_text(
        re.compile(
            rf"\b{re.escape(TARGET_TIME)}\b"
        )
    )

    count = await matches.count()

    if count == 0:
        return None

    for i in range(min(count, 10)):
        element = matches.nth(i)

        try:
            current = element

            for _ in range(10):
                text = (
                    await current.inner_text(
                        timeout=1500
                    )
                ).strip()

                lower = text.lower()

                if (
                    TARGET_TIME in text
                    and (
                        "sõiduauto" in lower
                        or "e-pilet" in lower
                        or "vali pilet" in lower
                        or "välja müüdud" in lower
                    )
                    and len(text) < 3000
                ):
                    return text

                current = current.locator("..")

        except Exception:
            pass

    return None


def analyse_availability(text):
    clean = " ".join(text.split())

    match = re.search(
        r"Sõiduauto\s*:?\s*(\d+)",
        clean,
        re.IGNORECASE,
    )

    if match:
        number = int(match.group(1))
        return number > 0, number

    lower = clean.lower()

    unavailable_words = [
        "välja müüdud",
        "e-pileteid ei ole",
        "pole saadaval",
        "ei ole saadaval",
        "sold out",
        "unavailable",
    ]

    if any(
        phrase in lower
        for phrase in unavailable_words
    ):
        return False, 0

    return False, None


# =========================
# CHECK PRAAMID
# =========================

async def check(page):
    now = datetime.now(TZ)

    print(
        now.strftime("%d.%m.%Y %H:%M:%S"),
        "- checking Praamid...",
        flush=True,
    )

    await page.goto(
        PRAAMID_URL,
        wait_until="domcontentloaded",
        timeout=40000,
    )

    print(
        "Praamid page loaded.",
        flush=True,
    )

    await page.wait_for_timeout(1500)

    await dismiss_cookies(page)

    print(
        "Navigating to 19.08.2026...",
        flush=True,
    )

    await move_to_target_date(page)

    print(
        "Target date selected.",
        flush=True,
    )

    await page.wait_for_timeout(1000)

    departure = await get_target_departure(page)

    if not departure:
        print(
            "19:00 departure not found.",
            flush=True,
        )
        return False, None

    print(
        "19:00 departure found.",
        flush=True,
    )

    available, count = analyse_availability(
        departure
    )

    print(
        "Passenger car availability:",
        count,
        flush=True,
    )

    return available, count


# =========================
# MAIN
# =========================

async def main():
    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID must be set."
        )

    print(
        "Starting Praamid monitor...",
        flush=True,
    )

    send_telegram(
        "✅ Praamid monitor is online!\n\n"
        "Rohuküla → Heltermaa\n"
        "19.08.2026 at 19:00\n"
        "Standard passenger car\n\n"
        "Checking every 3 minutes."
    )

    already_alerted = False

    async with async_playwright() as p:
        print(
            "Starting Chromium...",
            flush=True,
        )

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        print(
            "Chromium started.",
            flush=True,
        )

        page = await browser.new_page(
            locale="et-EE",
            timezone_id="Europe/Tallinn",
            viewport={
                "width": 1280,
                "height": 900,
            },
        )

        while True:
            try:
                if (
                    datetime.now(TZ).date()
                    > TARGET_DATE
                ):
                    send_telegram(
                        "Praamid monitor stopped — "
                        "the target date has passed."
                    )
                    break

                # TEMPORARY TEST MESSAGE
                send_telegram(
                    "🧪 Starting Praamid test check..."
                )

                available, count = await check(page)

                # TEMPORARY TEST RESULT
                send_telegram(
                    f"✅ Praamid test completed.\n"
                    f"{TARGET_ROUTE}\n"
                    f"{TARGET_DATE.strftime('%d.%m.%Y')} "
                    f"at {TARGET_TIME}\n"
                    f"Sõiduauto result: {count}"
                )

                if available:
                    if not already_alerted:
                        amount = (
                            str(count)
                            if count is not None
                            else "YES"
                        )

                        send_telegram(
                            "🚨🚨🚨 PRAAMIPILET AVAILABLE "
                            "🚨🚨🚨\n\n"
                            f"{TARGET_ROUTE}\n"
                            f"{TARGET_DATE.strftime('%d.%m.%Y')} "
                            f"at {TARGET_TIME}\n"
                            f"Sõiduauto availability: "
                            f"{amount}\n\n"
                            "BUY NOW:\n"
                            + PRAAMID_URL
                        )

                        already_alerted = True

                else:
                    already_alerted = False

            except Exception as error:
                print(
                    "Check failed:",
                    repr(error),
                    flush=True,
                )

                try:
                    send_telegram(
                        "⚠️ Praamid monitor check failed.\n\n"
                        f"{type(error).__name__}: "
                        f"{str(error)[:500]}"
                    )
                except Exception:
                    pass

            await asyncio.sleep(
                CHECK_INTERVAL_SECONDS
            )

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())