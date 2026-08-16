import os
import re
from datetime import date

from playwright.sync_api import sync_playwright


BASE = "https://www.praamid.ee/portal/ticket/departure"

# Browser-level safety timeouts.
# These are deliberately shorter than the worker watchdog.
PLAYWRIGHT_ACTION_TIMEOUT_MS = int(
    os.environ.get(
        "PLAYWRIGHT_ACTION_TIMEOUT_MS",
        "10000",
    )
)

PLAYWRIGHT_NAVIGATION_TIMEOUT_MS = int(
    os.environ.get(
        "PLAYWRIGHT_NAVIGATION_TIMEOUT_MS",
        "30000",
    )
)

PLAYWRIGHT_BROWSER_LAUNCH_TIMEOUT_MS = int(
    os.environ.get(
        "PLAYWRIGHT_BROWSER_LAUNCH_TIMEOUT_MS",
        "15000",
    )
)


ROUTES = {
    "RH": "Rohuküla → Heltermaa",
    "HR": "Heltermaa → Rohuküla",
    "VK": "Virtsu → Kuivastu",
    "KV": "Kuivastu → Virtsu",
}

MONTHS_ET = {
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


def dismiss_cookies(page):
    for text in (
        "Sain aru",
        "Nõustun",
        "Nõustu",
        "Accept",
        "Accept all",
        "Luba kõik",
    ):
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


def wait_for_praamid_app(page):
    """
    Praamid.ee is an Angular application.
    Wait until the actual ticket search form has rendered.
    """

    selectors = [
        "app-ticket-purchase-searchbar",
        "button.departure-select-date",
        "#departureDate",
        "app-datepicker",
    ]

    for selector in selectors:
        try:
            page.wait_for_selector(
                selector,
                state="attached",
                timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS,
            )
            return

        except Exception:
            pass

    body_text = (
        page.locator("body")
        .inner_text(timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS)
    )

    raise RuntimeError(
        "Praamid search form did not load. "
        f"Current URL: {page.url}. "
        f"First page text: {body_text[:500]}"
    )


def open_datepicker(page):
    selectors = [
        "button.datepicker-button.departure-select-date",
        "button.departure-select-date",
        "app-datepicker button",
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)

            if locator.count():
                locator.first.click(
                    timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS
                )

                page.wait_for_timeout(350)
                return

        except Exception:
            pass

    raise RuntimeError(
        "Praamid date picker button not found"
    )


def get_calendar_month_year(page):
    month = page.locator(
        ".flatpickr-current-month .cur-month"
    )

    year = page.locator(
        ".flatpickr-current-month .cur-year"
    )

    if not month.count() or not year.count():
        return None, None

    month_text = (
        month.first.inner_text()
        .strip()
        .lower()
    )

    month_lookup = {
        value.lower(): key
        for key, value
        in MONTHS_ET.items()
    }

    try:
        year_value = int(
            year.first.input_value()
        )

    except Exception:
        return None, None

    return (
        month_lookup.get(month_text),
        year_value,
    )


def select_target_date(
    page,
    target_date,
):
    open_datepicker(page)

    for _ in range(24):

        month, year = (
            get_calendar_month_year(page)
        )

        if (
            month == target_date.month
            and year == target_date.year
        ):
            break

        if month is None or year is None:
            raise RuntimeError(
                "Could not determine "
                "Praamid calendar month/year"
            )

        shown_index = (
            year * 12 + month
        )

        target_index = (
            target_date.year * 12
            + target_date.month
        )

        if shown_index < target_index:
            locator = page.locator(
                ".flatpickr-next-month"
            )

        else:
            locator = page.locator(
                ".flatpickr-prev-month"
            )

        if not locator.count():
            raise RuntimeError(
                "Could not navigate "
                "Praamid calendar"
            )

        locator.first.click(
            timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS
        )

        page.wait_for_timeout(200)

    month_name = MONTHS_ET[
        target_date.month
    ]

    selector = (
        '.flatpickr-day'
        f'[aria-label*="{target_date.day}. '
        f'{month_name}, '
        f'{target_date.year}"]'
    )

    candidates = page.locator(
        selector
    )

    if candidates.count():
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
                candidate.click(
                    timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS
                )

                page.wait_for_timeout(
                    1800
                )

                return

    # Fallback: use visible day number
    # inside the displayed month.
    candidates = page.locator(
        ".flatpickr-day"
        ":not(.prevMonthDay)"
        ":not(.nextMonthDay)"
    )

    for i in range(
        candidates.count()
    ):
        candidate = candidates.nth(i)

        try:
            text = (
                candidate.inner_text()
                .strip()
            )

            classes = (
                candidate.get_attribute(
                    "class"
                )
                or ""
            )

            if (
                text
                == str(target_date.day)
                and "flatpickr-disabled"
                not in classes
            ):
                candidate.click(
                    timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS
                )

                page.wait_for_timeout(
                    1800
                )

                return

        except Exception:
            pass

    raise RuntimeError(
        f"Could not find date "
        f"{target_date.strftime('%d.%m.%Y')} "
        "in Praamid calendar"
    )


def open_praamid(
    direction,
    target_date,
):
    if direction not in ROUTES:
        raise ValueError(
            "Unknown ferry direction"
        )

    playwright = (
        sync_playwright()
        .start()
    )

    browser = (
        playwright.chromium.launch(
            headless=True,
            timeout=PLAYWRIGHT_BROWSER_LAUNCH_TIMEOUT_MS,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
    )

    page = browser.new_page(
        locale="et-EE",
        timezone_id="Europe/Tallinn",
        viewport={
            "width": 1440,
            "height": 1400,
        },
    )

    # Applies to locator actions such as click(), inner_text(),
    # input_value(), wait_for_selector(), etc. unless overridden.
    page.set_default_timeout(
        PLAYWRIGHT_ACTION_TIMEOUT_MS
    )

    # Applies to page.goto() and other navigation operations.
    page.set_default_navigation_timeout(
        PLAYWRIGHT_NAVIGATION_TIMEOUT_MS
    )

    try:
        page.goto(
            f"{BASE}?direction={direction}",
            wait_until="domcontentloaded",
            timeout=PLAYWRIGHT_NAVIGATION_TIMEOUT_MS,
        )

        # Critical: wait for Angular
        # to render the real ticket form.
        wait_for_praamid_app(
            page
        )

        dismiss_cookies(
            page
        )

        select_target_date(
            page,
            target_date,
        )

        return (
            playwright,
            browser,
            page,
        )

    except Exception:
        close_browser_safely(
            browser,
            playwright,
        )
        raise



def close_browser_safely(
    browser,
    playwright,
):
    try:
        browser.close()
    except Exception:
        pass

    try:
        playwright.stop()
    except Exception:
        pass

def get_departure_times(
    direction,
    target_date,
):
    playwright, browser, page = (
        open_praamid(
            direction,
            target_date,
        )
    )

    try:
        try:
            text = (
                page.locator(
                    "#main-content"
                )
                .inner_text(
                    timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS
                )
            )

        except Exception:
            text = (
                page.locator("body")
                .inner_text(
                    timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS
                )
            )

        matches = re.findall(
            r"\b"
            r"([0-2]\d:[0-5]\d)"
            r"\s*[-–]\s*"
            r"([0-2]\d:[0-5]\d)"
            r"\b",
            text,
        )

        departures = []

        for departure, arrival in matches:
            if departure not in departures:
                departures.append(
                    departure
                )

        return departures

    finally:
        close_browser_safely(
            browser,
            playwright,
        )


def check_vehicle_availability(
    direction,
    target_date,
    target_time,
    vehicle_label="Sõiduauto",
):
    playwright, browser, page = (
        open_praamid(
            direction,
            target_date,
        )
    )

    try:
        body = (
            page.locator("body")
            .inner_text(
                timeout=5000
            )
        )

        body = " ".join(
            body.split()
        )

        index = body.find(
            target_time
        )

        if index < 0:
            raise RuntimeError(
                f"Departure "
                f"{target_time} "
                "not found"
            )

        chunk = body[
            index:index + 2000
        ]

        match = re.search(
            rf"{re.escape(vehicle_label)}"
            r"\s*:?\s*(\d+)",
            chunk,
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

        lower = chunk.lower()

        if (
            "välja müüdud"
            in lower
            or "sold out"
            in lower
        ):
            return False, 0

        # Conservative fallback:
        # do not generate a false alert.
        return False, None

    finally:
        close_browser_safely(
            browser,
            playwright,
        )
