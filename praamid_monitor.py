import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright


PRAAMID_URL = (
    "https://www.praamid.ee/portal/ticket/departure?direction=RH"
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TZ = ZoneInfo("Europe/Tallinn")


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
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
        timeout=30,
    )

    response.raise_for_status()


def send_telegram_photo(file_path, caption=""):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    with open(file_path, "rb") as photo:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
            },
            files={
                "photo": photo,
            },
            timeout=60,
        )

    response.raise_for_status()


# =========================================================
# COOKIE BANNER
# =========================================================

async def dismiss_cookies(page):
    possible_texts = [
        "Sain aru",
        "Nõustun",
        "Nõustu",
        "Accept",
        "Accept all",
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

                return

        except Exception:
            pass


# =========================================================
# DOM INSPECTION
# =========================================================

async def inspect_page(page):
    result = await page.evaluate(
        """
        () => {
            function elementInfo(el) {
                const rect = el.getBoundingClientRect();

                const style = window.getComputedStyle(el);

                return {
                    tag: el.tagName,
                    text: (el.innerText || el.textContent || '')
                        .trim()
                        .replace(/\\s+/g, ' ')
                        .slice(0, 300),

                    ariaLabel:
                        el.getAttribute('aria-label'),

                    title:
                        el.getAttribute('title'),

                    role:
                        el.getAttribute('role'),

                    href:
                        el.getAttribute('href'),

                    type:
                        el.getAttribute('type'),

                    className:
                        typeof el.className === 'string'
                            ? el.className.slice(0, 300)
                            : '',

                    id:
                        el.id || '',

                    visible:
                        !!(
                            rect.width ||
                            rect.height ||
                            el.getClientRects().length
                        ) &&
                        style.display !== 'none' &&
                        style.visibility !== 'hidden',

                    x:
                        Math.round(rect.x),

                    y:
                        Math.round(rect.y),

                    width:
                        Math.round(rect.width),

                    height:
                        Math.round(rect.height),

                    outerHTML:
                        el.outerHTML
                        ? el.outerHTML
                            .replace(/\\s+/g, ' ')
                            .slice(0, 700)
                        : ''
                };
            }


            const all = [
                ...document.querySelectorAll('*')
            ];


            const keywordElements = all.filter(el => {

                const text = [
                    el.innerText || '',
                    el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || ''
                ]
                .join(' ')
                .toLowerCase();

                return (
                    text.includes('järgmine kuupäev') ||
                    text.includes('eelmine kuupäev') ||
                    text.includes('12.08.2026') ||
                    text.includes('12. august') ||
                    text.includes('järgmine') ||
                    text.includes('eelmine')
                );
            });


            const interactive = [
                ...document.querySelectorAll(
                    'button, a, input, [role="button"], [tabindex]'
                )
            ];


            return {
                title: document.title,

                url: window.location.href,

                viewport: {
                    width: window.innerWidth,
                    height: window.innerHeight
                },

                keywordElements:
                    keywordElements
                        .slice(0, 60)
                        .map(elementInfo),

                interactiveElements:
                    interactive
                        .slice(0, 100)
                        .map(elementInfo)
            };
        }
        """
    )

    return result


def format_element(el, number):
    return (
        f"\n--- {number} ---\n"
        f"TAG: {el.get('tag')}\n"
        f"TEXT: {el.get('text')}\n"
        f"ARIA: {el.get('ariaLabel')}\n"
        f"TITLE: {el.get('title')}\n"
        f"ROLE: {el.get('role')}\n"
        f"HREF: {el.get('href')}\n"
        f"ID: {el.get('id')}\n"
        f"CLASS: {el.get('className')}\n"
        f"VISIBLE: {el.get('visible')}\n"
        f"POSITION: "
        f"{el.get('x')},{el.get('y')} "
        f"{el.get('width')}x{el.get('height')}\n"
        f"HTML: {el.get('outerHTML')}\n"
    )


# =========================================================
# SEND DIAGNOSTICS
# =========================================================

async def send_diagnostics(page):
    data = await inspect_page(page)

    send_telegram(
        "🔬 PRAAMID DOM INSPECTION\n\n"
        f"URL:\n{data['url']}\n\n"
        f"Title: {data['title']}\n"
        f"Viewport: "
        f"{data['viewport']['width']}x"
        f"{data['viewport']['height']}\n\n"
        f"Keyword elements found: "
        f"{len(data['keywordElements'])}\n"
        f"Interactive elements found: "
        f"{len(data['interactiveElements'])}"
    )

    keyword_elements = data["keywordElements"]

    if keyword_elements:
        chunks = []
        current = ""

        for i, el in enumerate(
            keyword_elements[:30],
            start=1,
        ):
            block = format_element(el, i)

            if len(current) + len(block) > 3500:
                chunks.append(current)
                current = block
            else:
                current += block

        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks, start=1):
            send_telegram(
                f"🔎 DATE ELEMENTS {i}/{len(chunks)}\n"
                + chunk
            )

    else:
        send_telegram(
            "⚠️ No DOM elements containing "
            "Järgmine/Eelmine/date text were found."
        )


    # Now send interactive elements which look relevant
    relevant = []

    for el in data["interactiveElements"]:
        combined = " ".join(
            [
                str(el.get("text") or ""),
                str(el.get("ariaLabel") or ""),
                str(el.get("title") or ""),
                str(el.get("href") or ""),
                str(el.get("className") or ""),
            ]
        ).lower()

        if (
            "kuup" in combined
            or "date" in combined
            or "next" in combined
            or "prev" in combined
            or "järgm" in combined
            or "eelm" in combined
            or "calendar" in combined
        ):
            relevant.append(el)


    if relevant:
        chunks = []
        current = ""

        for i, el in enumerate(
            relevant[:40],
            start=1,
        ):
            block = format_element(el, i)

            if len(current) + len(block) > 3500:
                chunks.append(current)
                current = block
            else:
                current += block

        if current:
            chunks.append(current)

        for i, chunk in enumerate(chunks, start=1):
            send_telegram(
                f"🖱 RELEVANT CLICKABLES "
                f"{i}/{len(chunks)}\n"
                + chunk
            )

    else:
        send_telegram(
            "ℹ️ No obviously date-related "
            "interactive controls were found."
        )


# =========================================================
# MAIN
# =========================================================

async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN missing"
        )

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_CHAT_ID missing"
        )

    send_telegram(
        "🧪 Praamid date-selector diagnostic started."
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

        try:
            send_telegram(
                "🌐 Opening Praamid.ee..."
            )

            await page.goto(
                PRAAMID_URL,
                wait_until="networkidle",
                timeout=60000,
            )

            await page.wait_for_timeout(3000)

            await dismiss_cookies(page)

            await page.wait_for_timeout(1500)

            screenshot_path = (
                "/tmp/praamid_debug.png"
            )

            await page.screenshot(
                path=screenshot_path,
                full_page=True,
            )

            send_telegram_photo(
                screenshot_path,
                "📸 What Railway/Playwright "
                "currently sees on Praamid.ee"
            )

            await send_diagnostics(page)

            send_telegram(
                "✅ Diagnostic finished.\n\n"
                "Send me the DATE ELEMENTS / "
                "RELEVANT CLICKABLES messages."
            )

        except Exception as error:
            send_telegram(
                "❌ DIAGNOSTIC ERROR\n\n"
                f"{type(error).__name__}\n"
                f"{str(error)[:2000]}"
            )

        # Keep container alive so Railway
        # does not repeatedly rerun the diagnostic.
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())