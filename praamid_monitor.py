import asyncio
import os
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


# =========================================================
# TARGET
# =========================================================

TARGET_DATE = date(2026, 8, 19)
TARGET_TIME = "19:00"
TARGET_ROUTE = "Rohuküla → Heltermaa"

PRAAMID_URL = (
    "https://www.praamid.ee/portal/ticket/departure?direction=RH"
)

CHECK_INTERVAL_SECONDS = 180
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

                print(
                    f"Cookie button clicked: {text}",
                    flush=True,
                )

                await page.wait_for_timeout(500)
                return

        except Exception:
            pass


# =========================================================
# DATE RECOGNITION
# =========================================================

async def find_dates_on_page(page):
    text = await get_page_text(page)

    months = {
        "jaanuar": 1,
        "veebruar": 2,
        "märts": 3,
        "aprill": 4,
        "mai": 5,
        "juuni": 6,
        "juuli": 7,
        "august": 8,
        "september": 9,
        "oktoober": 10,
        "november": 11,
        "detsember": 12,
    }

    found = []

    # Praamid format, e.g. "12. august"
    for month_name, month_number in months.items():
        pattern = rf"\b(\d{{1,2}})\.\s*{month_name}\b"

        for match in re.finditer(
            pattern,
            text,
            re.IGNORECASE
        ):
            day = int(match.group(1))

            try:
                found.append(
                    date(
                        TARGET_DATE.year,
                        month_number,
                        day
                    )
                )
            except ValueError:
                pass

    # Numeric dates, just in case
    for match in re.finditer(
        r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b",
        text
    ):
        day, month, year = map(
            int,
            match.groups()
        )

        try:
            found.append(
                date(year, month, day)
            )
        except ValueError:
            pass

    return found


async def find_visible_date(page):
    dates = await find_dates_on_page(page)

    if not dates:
        return None

    return min(
        dates,
        key=lambda d: abs(
            (d - TARGET_DATE).days
        ),
    )


# =========================================================
# NEXT-DAY NAVIGATION
# =========================================================

async def click_next_day(page):
    print(
        "Trying to find next-date control...",
        flush=True,
    )

    # Method 1: visible text
    try:
        locator = page.get_by_text(
            "Järgmine kuupäev",
            exact=True,
        )

        if await locator.count():
            await locator.first.click(
                timeout=3000
            )

            await page.wait_for_timeout(800)
            return True

    except Exception:
        pass

    # Method 2: partial text
    try:
        locator = page.get_by_text(
            "Järgmine kuupäev",
            exact=False,
        )

        if await locator.count():
            await locator.first.click(
                timeout=3000
            )

            await page.wait_for_timeout(800)
            return True

    except Exception:
        pass

    # Method 3: aria-label
    try:
        locator = page.locator(
            '[aria-label*="Järgmine"]'
        )

        if await locator.count():
            await locator.first.click(
                timeout=3000
            )

            await page.wait_for_timeout(800)
            return True

    except Exception:
        pass

    # Method 4: title attribute
    try:
        locator = page.locator(
            '[title*="Järgmine"]'
        )

        if await locator.count():
            await locator.first.click(
                timeout=3000
            )

            await page.wait_for_timeout(800)
            return True

    except Exception:
        pass

    # Method 5: scan clickable elements
    try:
        candidates = page.locator(
            "button, a, [role='button']"
        )

        count = await candidates.count()

        for i in range(min(count, 300)):
            element = candidates.nth(i)

            try:
                text = (
                    await element.inner_text()
                ).strip()

                aria = (
                    await element.get_attribute(
                        "aria-label"
                    )
                    or ""
                )

                title = (
                    await element.get_attribute(
                        "title"
                    )
                    or ""
                )

                combined = (
                    text
                    + " "
                    + aria
                    + " "
                    + title
                ).lower()

                if (
                    "järgmine kuupäev" in combined
                    or "next date" in combined
                ):
                    await element.click(
                        timeout=3000
                    )

                    await page.wait_for_timeout(800)
                    return True

            except Exception:
                pass

    except Exception:
        pass

    # Method 6: JavaScript scan
    try:
        result = await page.evaluate(
            """
            () => {
                const all = [
                    ...document.querySelectorAll(
                        'button,a,[role="button"]'
                    )
                ];

                for (const el of all) {
                    const text = [
                        el.innerText || '',
                        el.getAttribute('aria-label') || '',
                        el.getAttribute('title') || ''
                    ].join(' ').toLowerCase();

                    if (
                        text.includes('järgmine kuupäev') ||
                        text.includes('next date')
                    ) {
                        el.click();
                        return text;
                    }
                }

                return null;
            }
            """
        )

        if result:
            await page.wait_for_timeout(800)
            return True

    except Exception:
        pass

    return False


# =========================================================
# MOVE TO 19 AUGUST
# =========================================================

async def move_to_target_date(page):
    current = await find_visible_date(page)

    print(
        "Detected initial date:",
        current,
        flush=True,
    )

    if current is None:
        body_text = await get_page_text(page)

        send_telegram(
            "⚠️ Could not determine the currently "
            "displayed Praamid date.\n\n"
            "First visible page text:\n"
            + body_text[:1500]
        )

        raise RuntimeError(
            "Could not determine current date."
        )

    for attempt in range(40):
        print(
            f"Current date: {current} | "
            f"Target: {TARGET_DATE}",
            flush=True,
        )

        if current == TARGET_DATE:
            print(
                "Target date reached!",
                flush=True,
            )
            return

        if current > TARGET_DATE:
            raise RuntimeError(
                f"Displayed date {current} is after "
                f"target date {TARGET_DATE}."
            )

        clicked = await click_next_day(page)

        if not clicked:
            body_text = await get_page_text(page)

            send_telegram(
                "⚠️ NEXT-DAY BUTTON NOT FOUND\n\n"
                f"Current detected date: {current}\n\n"
                "Visible Praamid page text:\n"
                + body_text[:1800]
            )

            raise RuntimeError(
                "Could not find the next-day button."
            )

        await page.wait_for_timeout(800)

        new_date = await find_visible_date(page)

        print(
            "Detected date after click:",
            new_date,
            flush=True,
        )

        if new_date:
            current = new_date
        else:
            current = date.fromordinal(
                current.toordinal() + 1
            )

    raise RuntimeError(
        "Exceeded maximum date navigation attempts."
    )


# =========================================================
# FIND 19:00 DEPARTURE
# =========================================================

async def get_target_departure(page):
    body = await get_page_text(page)

    print(
        "Searching for target departure...",
        flush=True,
    )

    if TARGET_TIME not in body:
        send_telegram(
            "⚠️ 19:00 was not found on the "
            "selected Praamid date page."
        )

        return None

    matches = page.get_by_text(
        re.compile(
            rf"\b{re.escape(TARGET_TIME)}\b"
        )
    )

    count = await matches.count()

    print(
        "19:00 text matches:",
        count,
        flush=True,
    )

    for i in range(min(count, 20)):
        element = matches.nth(i)

        try:
            current = element

            for _ in range(12):
                text = (
                    await current.inner_text(
                        timeout=1500
                    )
                ).strip()

                lower = text.lower()

                if (
                    TARGET_TIME in text
                    and len(text) < 4000
                    and (
                        "sõiduauto" in lower
                        or "vali pilet" in lower
                        or "e-pilet" in lower
                        or "välja müüdud" in lower
                    )
                ):
                    return text

                current = current.locator("..")

        except Exception:
            pass

    position = body.find(
        TARGET_TIME
    )

    if position >= 0:
        start = max(
            0,
            position - 500,
        )

        end = min(
            len(body),
            position + 1200,
        )

        context = body[
            start:end
        ]

        send_telegram(
            "⚠️ Found 19:00 but could not "
            "identify its departure card.\n\n"
            + context
        )

    return None


# =========================================================
# AVAILABILITY
# =========================================================

def analyse_availability(text):
    clean = " ".join(
        text.split()
    )

    print(
        "Analysing departure:",
        clean[:1200],
        flush=True,
    )

    patterns = [
        r"Sõiduauto\s*:?\s*(\d+)",
        r"Sõiduauto.*?(\d+)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            clean,
            re.IGNORECASE,
        )

        if match:
            number = int(
                match.group(1)
            )

            return (
                number > 0,
                number,
            )

    lower = clean.lower()

    sold_out_terms = [
        "välja müüdud",
        "e-pileteid ei ole",
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
# ONE CHECK
# =========================================================

async def check(page):
    print(
        "\n==============================",
        flush=True,
    )

    print(
        datetime.now(TZ).strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
        "Starting Praamid check",
        flush=True,
    )

    await page.goto(
        PRAAMID_URL,
        wait_until="domcontentloaded",
        timeout=40000,
    )

    print(
        "Praamid page opened.",
        flush=True,
    )

    await page.wait_for_timeout(2500)

    await dismiss_cookies(page)

    await move_to_target_date(page)

    await page.wait_for_timeout(1500)

    departure = await get_target_departure(
        page
    )

    if not departure:
        raise RuntimeError(
            "19:00 departure card could not "
            "be identified."
        )

    available, count = (
        analyse_availability(
            departure
        )
    )

    print(
        "FINAL RESULT:",
        available,
        count,
        flush=True,
    )

    return available, count


# =========================================================
# MAIN
# =========================================================

async def main():
    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        raise RuntimeError(
            "Telegram Railway variables missing."
        )

    send_telegram(
        "✅ NEW Praamid monitor version started!\n\n"
        "Rohuküla → Heltermaa\n"
        "19.08.2026 at 19:00\n"
        "Sõiduauto"
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
                "width": 1280,
                "height": 900,
            },
        )

        already_alerted = False

        while True:
            try:
                available, count = (
                    await check(page)
                )

                # TEMPORARY TEST MESSAGE
                send_telegram(
                    "🔎 Praamid check successful!\n\n"
                    f"{TARGET_ROUTE}\n"
                    f"{TARGET_DATE.strftime('%d.%m.%Y')} "
                    f"at {TARGET_TIME}\n"
                    f"Sõiduauto result: {count}"
                )

                if available:
                    if not already_alerted:
                        send_telegram(
                            "🚨🚨🚨 PRAAMIPILET "
                            "AVAILABLE 🚨🚨🚨\n\n"
                            f"{TARGET_ROUTE}\n"
                            f"{TARGET_DATE.strftime('%d.%m.%Y')} "
                            f"at {TARGET_TIME}\n"
                            f"Sõiduauto: {count}\n\n"
                            "BUY NOW:\n"
                            + PRAAMID_URL
                        )

                        already_alerted = True

                else:
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