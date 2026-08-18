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
        "20000",
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

    Important: wait for all known form selectors in ONE locator call.
    The old version waited up to ACTION_TIMEOUT separately for every
    selector, which could make one bad page consume 40-50 seconds.
    """

    combined_selector = (
        "app-ticket-purchase-searchbar, "
        "button.departure-select-date, "
        "#departureDate, "
        "app-datepicker"
    )

    try:
        page.locator(
            combined_selector
        ).first.wait_for(
            state="attached",
            timeout=PLAYWRIGHT_ACTION_TIMEOUT_MS,
        )
        return

    except Exception:
        pass

    try:
        body_text = (
            page.locator("body")
            .inner_text(timeout=3000)
        )
    except Exception:
        body_text = "<body text unavailable>"

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


class PraamidBrowserSession:
    """
    One Playwright + Chromium instance that can perform many tracker checks.

    Each tracker gets a FRESH browser context/page, so cookies/page state cannot
    leak from one tracker into another, while Chromium itself is launched only
    once for the full worker cycle.
    """

    def __init__(self):
        self.playwright = None
        self.browser = None

    def __enter__(self):
        self.playwright = sync_playwright().start()

        try:
            self.browser = self.playwright.chromium.launch(
                headless=True,
                timeout=PLAYWRIGHT_BROWSER_LAUNCH_TIMEOUT_MS,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-extensions",
                    "--disable-sync",
                ],
            )
        except Exception:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
            raise

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def restart(self):
        """Fully rebuild Playwright + Chromium after a failed browser/session."""
        self.close()
        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch(
                headless=True,
                timeout=PLAYWRIGHT_BROWSER_LAUNCH_TIMEOUT_MS,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-default-apps",
                    "--disable-extensions",
                    "--disable-sync",
                ],
            )
        except Exception:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None
            raise
        return self

    def close(self):
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None

        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

    def _new_page(self):
        if self.browser is None:
            raise RuntimeError("Praamid browser session is not open")

        context = self.browser.new_context(
            locale="et-EE",
            timezone_id="Europe/Tallinn",
            viewport={
                "width": 1440,
                "height": 1400,
            },
        )

        page = context.new_page()

        page.set_default_timeout(
            PLAYWRIGHT_ACTION_TIMEOUT_MS
        )

        page.set_default_navigation_timeout(
            PLAYWRIGHT_NAVIGATION_TIMEOUT_MS
        )

        return context, page

    def _prepare_page(
        self,
        direction,
        target_date,
    ):
        if direction not in ROUTES:
            raise ValueError("Unknown ferry direction")

        context, page = self._new_page()

        try:
            page.goto(
                f"{BASE}?direction={direction}",
                wait_until="domcontentloaded",
                timeout=PLAYWRIGHT_NAVIGATION_TIMEOUT_MS,
            )

            page.wait_for_timeout(750)

            wait_for_praamid_app(page)
            dismiss_cookies(page)
            select_target_date(page, target_date)

            return context, page

        except Exception:
            try:
                context.close()
            except Exception:
                pass
            raise

    def get_departure_times(
        self,
        direction,
        target_date,
    ):
        context, page = self._prepare_page(
            direction,
            target_date,
        )

        try:
            try:
                text = (
                    page.locator("#main-content")
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
                    departures.append(departure)

            return departures

        finally:
            try:
                context.close()
            except Exception:
                pass

    def check_vehicle_availability(
        self,
        direction,
        target_date,
        target_time,
        vehicle_label="Sõiduauto",
    ):
        context, page = self._prepare_page(
            direction,
            target_date,
        )

        try:
            body = (
                page.locator("body")
                .inner_text(timeout=5000)
            )

            body = " ".join(body.split())

            index = body.find(target_time)

            if index < 0:
                raise RuntimeError(
                    f"Departure {target_time} not found"
                )

            chunk = body[index:index + 2000]

            match = re.search(
                rf"{re.escape(vehicle_label)}"
                r"\s*:?\s*(\d+)",
                chunk,
                re.IGNORECASE,
            )

            if match:
                count = int(match.group(1))
                return count > 0, count

            lower = chunk.lower()

            if (
                "välja müüdud" in lower
                or "sold out" in lower
            ):
                return False, 0

            # Conservative fallback: never create a false-positive alert.
            return False, None

        finally:
            try:
                context.close()
            except Exception:
                pass


def get_departure_times(
    direction,
    target_date,
):
    """
    Backwards-compatible public function used by the web app.
    It uses one short-lived shared-session object for this one web request.
    """
    with PraamidBrowserSession() as session:
        return session.get_departure_times(
            direction,
            target_date,
        )


def check_vehicle_availability(
    direction,
    target_date,
    target_time,
    vehicle_label="Sõiduauto",
):
    """
    Backwards-compatible one-off availability function.
    The background worker no longer calls this per tracker; it uses
    PraamidBrowserSession once for the whole cycle through check_cycle.py.
    """
    with PraamidBrowserSession() as session:
        return session.check_vehicle_availability(
            direction,
            target_date,
            target_time,
            vehicle_label,
        )
