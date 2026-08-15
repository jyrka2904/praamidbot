import re
from datetime import date
from playwright.sync_api import sync_playwright

BASE = "https://www.praamid.ee/portal/ticket/departure"

ROUTES = {
    "RH": "Rohuküla → Heltermaa",
    "HR": "Heltermaa → Rohuküla",
    "VK": "Virtsu → Kuivastu",
    "KV": "Kuivastu → Virtsu",
}

MONTHS_ET = {
    1: "Jaanuar", 2: "Veebruar", 3: "Märts", 4: "Aprill",
    5: "Mai", 6: "Juuni", 7: "Juuli", 8: "August",
    9: "September", 10: "Oktoober", 11: "November", 12: "Detsember",
}

def _dismiss(page):
    for txt in ("Sain aru", "Nõustun", "Accept", "Accept all"):
        try:
            loc = page.get_by_text(txt, exact=True)
            if loc.count():
                loc.first.click(timeout=1000)
                break
        except Exception:
            pass

def _select_date(page, target: date):
    # Open Flatpickr.
    candidates = [
        "button.datepicker-button.departure-select-date",
        ".departure-select-date",
        "input.flatpickr-input",
    ]
    opened = False
    for sel in candidates:
        try:
            loc = page.locator(sel)
            if loc.count():
                loc.first.click(timeout=3000)
                opened = True
                break
        except Exception:
            pass
    if not opened:
        raise RuntimeError("Praamid date picker not found")

    page.wait_for_timeout(250)

    # Navigate calendar using visible month/year.
    for _ in range(18):
        month_loc = page.locator(".flatpickr-current-month .cur-month")
        year_loc = page.locator(".flatpickr-current-month .cur-year")
        if not month_loc.count() or not year_loc.count():
            break

        month_text = month_loc.first.inner_text().strip().lower()
        month_map = {v.lower(): k for k, v in MONTHS_ET.items()}
        shown_month = month_map.get(month_text)
        try:
            shown_year = int(year_loc.first.input_value())
        except Exception:
            shown_year = target.year

        if shown_month == target.month and shown_year == target.year:
            break

        shown_idx = shown_year * 12 + (shown_month or target.month)
        target_idx = target.year * 12 + target.month
        sel = ".flatpickr-next-month" if shown_idx < target_idx else ".flatpickr-prev-month"
        nav = page.locator(sel)
        if not nav.count():
            raise RuntimeError("Could not navigate Praamid calendar")
        nav.first.click(timeout=2000)
        page.wait_for_timeout(150)

    # Prefer aria-label containing the exact date.
    month = MONTHS_ET[target.month]
    possible = [
        f'.flatpickr-day[aria-label*="{target.day}. {month}, {target.year}"]',
        f'.flatpickr-day[aria-label*="{target.day}. {month} {target.year}"]',
    ]
    for sel in possible:
        loc = page.locator(sel)
        for i in range(loc.count()):
            el = loc.nth(i)
            cls = el.get_attribute("class") or ""
            if "flatpickr-disabled" not in cls:
                el.click(timeout=3000)
                page.wait_for_timeout(1200)
                return

    # Fallback: visible day number in current month.
    days = page.locator(".flatpickr-day:not(.prevMonthDay):not(.nextMonthDay)")
    for i in range(days.count()):
        el = days.nth(i)
        try:
            if el.inner_text().strip() == str(target.day):
                cls = el.get_attribute("class") or ""
                if "flatpickr-disabled" not in cls:
                    el.click(timeout=3000)
                    page.wait_for_timeout(1200)
                    return
        except Exception:
            pass

    raise RuntimeError("Requested date not selectable on Praamid.ee")

def _open_page(direction, target_date):
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    page = browser.new_page(locale="et-EE", timezone_id="Europe/Tallinn", viewport={"width": 1440, "height": 1400})
    try:
        page.goto(f"{BASE}?direction={direction}", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1000)
        _dismiss(page)
        _select_date(page, target_date)
        return p, browser, page
    except Exception:
        browser.close()
        p.stop()
        raise

def get_departure_times(direction, target_date):
    if direction not in ROUTES:
        raise ValueError("Unknown route")
    p, browser, page = _open_page(direction, target_date)
    try:
        text = page.locator("body").inner_text()
        # Departure rows normally contain e.g. 19:00 - 20:15.
        pairs = re.findall(r"\b([0-2]\d:[0-5]\d)\s*[-–]\s*([0-2]\d:[0-5]\d)\b", text)
        result = []
        for dep, _arr in pairs:
            if dep not in result:
                result.append(dep)
        return result
    finally:
        browser.close()
        p.stop()

def check_vehicle_availability(direction, target_date, target_time, vehicle_label="Sõiduauto"):
    p, browser, page = _open_page(direction, target_date)
    try:
        body = " ".join(page.locator("body").inner_text().split())

        # Isolate a useful slice around the requested departure.
        idx = body.find(target_time)
        if idx < 0:
            raise RuntimeError(f"Departure {target_time} not found")
        chunk = body[idx:idx + 1800]

        # The current Praamid page has exposed labels such as "Sõiduauto: 0".
        m = re.search(rf"{re.escape(vehicle_label)}\s*:?\s*(\d+)", chunk, re.I)
        if m:
            count = int(m.group(1))
            return count > 0, count

        # Conservative fallback: don't send an SMS unless availability is explicit.
        lower = chunk.lower()
        if "välja müüdud" in lower or "sold out" in lower:
            return False, 0

        return False, None
    finally:
        browser.close()
        p.stop()
