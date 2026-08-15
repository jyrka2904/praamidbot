import os
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from database import init_db, get_conn
from praamid import ROUTES, get_departure_times


TZ = ZoneInfo("Europe/Tallinn")
UTC = ZoneInfo("UTC")
MAX_OPEN_TRACKERS = 5
SUPPORTED_LANGUAGES = {"et", "en"}

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

twilio = Client(
    os.environ["TWILIO_ACCOUNT_SID"],
    os.environ["TWILIO_AUTH_TOKEN"],
)

VERIFY_SID = os.environ["TWILIO_VERIFY_SERVICE_SID"]

init_db()


TRANSLATIONS = {
    "et": {
        "app_name": "Praamid.ee Tracker",
        "tagline": "Praamipiletite saadavuse teavitused",
        "login_title": "Tere tulemast tagasi",
        "login_subtitle": "Logi sisse, et hallata oma aktiivseid jälgijaid.",
        "phone": "Telefoninumber",
        "password": "Parool",
        "login": "Logi sisse",
        "logout": "Logi välja",
        "no_account": "Pole veel kontot?",
        "create_one": "Loo konto",
        "already_account": "Konto on juba olemas?",
        "create_account": "Loo konto",
        "signup_subtitle": "Kinnitame sinu telefoninumbri SMS-i teel.",
        "send_code": "Saada kinnituskood",
        "verify_title": "Sisesta kinnituskood",
        "verify_subtitle": "Saatsime kinnituskoodi numbrile",
        "sms_code": "SMS-kood",
        "verify_phone": "Kinnita telefon",
        "password_title": "Loo parool",
        "password_subtitle": "Kasuta vähemalt 8 tähemärki.",
        "confirm_password": "Korda parooli",
        "create_tracker": "Loo uus jälgija",
        "create_tracker_sub": "Vali väljumine ja me jälgime sõiduauto piletite saadavust automaatselt.",
        "route": "Suund",
        "date": "Kuupäev",
        "departure": "Väljumine",
        "select_route_date": "Vali esmalt suund ja kuupäev",
        "loading_departures": "Laen väljumisi…",
        "checking_schedule": "Kontrollin Praamid.ee sõiduplaani…",
        "choose_departure": "Vali väljumine",
        "no_departures": "Väljumisi ei leitud",
        "no_departures_date": "Sellel kuupäeval ei leitud väljumisi.",
        "could_not_load": "Väljumisi ei õnnestunud laadida",
        "scheduled_found": "plaanilist väljumist leitud.",
        "up_to": "Kuni",
        "trackers": "jälgijat",
        "checked_every": "Kontroll iga 2–3 minuti järel",
        "start_tracking": "Alusta jälgimist",
        "max_reached": "Aktiivsete jälgijate maksimum on täis. Uue lisamiseks eemalda üks olemasolev.",
        "active_trackers": "Aktiivsed jälgijad",
        "active": "aktiivset",
        "active_sub": "Saadame SMS-i ja eemaldame jälgija automaatselt, kui vaba koht leitakse.",
        "no_active": "Aktiivseid jälgijaid pole",
        "no_active_sub": "Lisa ülevalt väljumine ja alustame jälgimist.",
        "monitoring": "Jälgimine",
        "checking_every_short": "Kontroll iga 2–3 min järel",
        "paused": "Peatatud",
        "paused_sub": "Jälgimine on ajutiselt peatatud",
        "last_checked": "Viimati kontrollitud",
        "waiting_first": "Ootan esimest kontrolli",
        "last_result": "Viimane tulemus",
        "available": "saadaval",
        "check_error": "Kontrolli viga",
        "pause": "Peata",
        "resume": "Jätka",
        "remove": "Eemalda",
        "how_it_works": "Kuidas see töötab",
        "step1": "Lisad väljumise, mida soovid jälgida",
        "step2": "Kontrollime saadavust iga 2–3 minuti järel",
        "step3": "Saad SMS-i, kui sõiduauto pilet muutub saadavaks",
        "step4": "Jälgija eemaldatakse automaatselt",
        "good_to_know": "Hea teada",
        "good1": "Teavitused saadetakse sinu kinnitatud telefoninumbrile",
        "good2": "Sul saab korraga olla kuni 5 aktiivset jälgijat",
        "good3": "Jälgija eemaldatakse pärast teavituse saatmist",
        "good4": "Kui pilet jääb saamata, lisa sama jälgija uuesti",
        "invalid_login": "Vale telefoninumber või parool.",
        "bad_code": "Kinnituskood on vale või aegunud.",
        "password_short": "Parool peab olema vähemalt 8 tähemärki pikk.",
        "password_mismatch": "Paroolid ei ühti.",
        "invalid_route": "Vigane suund.",
        "schedule_fail": "Sõiduplaani ei õnnestunud kontrollida. Proovi uuesti.",
        "invalid_departure": "Valitud ajal sellel suunal praami ei välju.",
        "max_5": "Ühel kasutajal saab olla maksimaalselt 5 aktiivset jälgijat.",
        "tracker_activated": "Jälgija aktiveeritud. Teavitame sind, kui sõiduauto pilet muutub saadavaks.",
        "tracker_removed": "Jälgija eemaldatud.",
    },
    "en": {
        "app_name": "Praamid.ee Tracker",
        "tagline": "Ferry availability alerts",
        "login_title": "Welcome back",
        "login_subtitle": "Sign in to manage your active ferry trackers.",
        "phone": "Phone number",
        "password": "Password",
        "login": "Log in",
        "logout": "Log out",
        "no_account": "No account?",
        "create_one": "Create one",
        "already_account": "Already registered?",
        "create_account": "Create account",
        "signup_subtitle": "We'll verify your phone number by SMS.",
        "send_code": "Send verification code",
        "verify_title": "Enter verification code",
        "verify_subtitle": "We sent a code to",
        "sms_code": "SMS code",
        "verify_phone": "Verify phone",
        "password_title": "Create a password",
        "password_subtitle": "Use at least 8 characters.",
        "confirm_password": "Confirm password",
        "create_tracker": "Create a new tracker",
        "create_tracker_sub": "Choose a scheduled departure and we'll monitor passenger-car availability automatically.",
        "route": "Route",
        "date": "Date",
        "departure": "Departure",
        "select_route_date": "Select route and date first",
        "loading_departures": "Loading departures…",
        "checking_schedule": "Checking the live Praamid.ee schedule…",
        "choose_departure": "Choose departure",
        "no_departures": "No departures found",
        "no_departures_date": "No scheduled departures were found for this date.",
        "could_not_load": "Could not load departures",
        "scheduled_found": "scheduled departures found.",
        "up_to": "Up to",
        "trackers": "trackers",
        "checked_every": "Checked every 2–3 minutes",
        "start_tracking": "Start tracking",
        "max_reached": "You've reached the maximum number of active trackers. Remove one before adding another.",
        "active_trackers": "Active trackers",
        "active": "active",
        "active_sub": "We'll send an SMS alert and remove the tracker when availability is found.",
        "no_active": "No active trackers",
        "no_active_sub": "Create one above and we'll start monitoring.",
        "monitoring": "Monitoring",
        "checking_every_short": "Checking every 2–3 min",
        "paused": "Paused",
        "paused_sub": "Monitoring temporarily paused",
        "last_checked": "Last checked",
        "waiting_first": "Waiting for first check",
        "last_result": "Last result",
        "available": "available",
        "check_error": "Check error",
        "pause": "Pause",
        "resume": "Resume",
        "remove": "Remove",
        "how_it_works": "How it works",
        "step1": "You add a departure to watch",
        "step2": "We check availability every 2–3 minutes",
        "step3": "You get an SMS when a passenger-car ticket is available",
        "step4": "The tracker is removed automatically",
        "good_to_know": "Good to know",
        "good1": "Alerts are sent to your verified phone number",
        "good2": "You can have up to 5 active trackers",
        "good3": "The tracker is removed after an alert is sent",
        "good4": "If you miss the ticket, simply add the tracker again",
        "invalid_login": "Invalid phone number or password.",
        "bad_code": "Incorrect or expired verification code.",
        "password_short": "Password must be at least 8 characters.",
        "password_mismatch": "Passwords do not match.",
        "invalid_route": "Invalid route.",
        "schedule_fail": "Could not verify the ferry schedule. Try again.",
        "invalid_departure": "That is not a scheduled departure for the selected route/date.",
        "max_5": "Maximum 5 open trackers per user.",
        "tracker_activated": "Tracker activated. We'll notify you if a passenger-car ticket becomes available.",
        "tracker_removed": "Tracker removed.",
    },
}


def normalize_language(value):
    return value if value in SUPPORTED_LANGUAGES else "et"


def get_language():
    if "lang" in session:
        return normalize_language(session["lang"])

    uid = session.get("user_id")
    if uid:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT language FROM users WHERE id=%s",
                    (uid,),
                )
                row = cur.fetchone()
                if row:
                    lang = normalize_language(row["language"])
                    session["lang"] = lang
                    return lang

    return "et"


def tr(key):
    lang = get_language()
    return TRANSLATIONS[lang].get(key, key)


def template_context(**kwargs):
    lang = get_language()
    return {
        "lang": lang,
        "t": TRANSLATIONS[lang],
        **kwargs,
    }


def normalize_phone(raw):
    value = re.sub(r"[^\d+]", "", raw or "")

    if value.startswith("00"):
        value = "+" + value[2:]

    if value.startswith("5") and not value.startswith("+"):
        value = "+372" + value

    if not re.fullmatch(r"\+\d{7,15}", value):
        if get_language() == "et":
            raise ValueError(
                "Kasuta rahvusvahelist telefoninumbrit, nt +37255512345."
            )
        raise ValueError(
            "Use an international phone number, e.g. +37255512345."
        )

    return value


def require_user():
    uid = session.get("user_id")

    if not uid:
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE id=%s",
                (uid,),
            )
            return cur.fetchone()


def expire_old():
    now = datetime.now(TZ)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM trackers
                WHERE travel_date < %s
                   OR (
                        travel_date = %s
                        AND departure_time <= %s
                   )
            """, (
                now.date(),
                now.date(),
                now.time().replace(tzinfo=None),
            ))


@app.before_request
def auth_guard():
    allowed = {
        "login",
        "signup",
        "verify",
        "create_password",
        "health",
        "static",
        "set_language",
    }

    if (
        request.endpoint
        and request.endpoint not in allowed
        and not session.get("user_id")
    ):
        return redirect(url_for("login"))


@app.get("/language/<lang>")
def set_language(lang):
    lang = normalize_language(lang)
    session["lang"] = lang

    uid = session.get("user_id")
    if uid:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET language=%s
                    WHERE id=%s
                    """,
                    (lang, uid),
                )

    next_url = request.args.get("next") or request.referrer or url_for("dashboard")
    return redirect(next_url)


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        try:
            phone = normalize_phone(
                request.form["phone_number"]
            )

            twilio.verify.v2.services(
                VERIFY_SID
            ).verifications.create(
                to=phone,
                channel="sms",
            )

            session["pending_phone"] = phone

            return redirect(
                url_for("verify")
            )

        except (ValueError, TwilioRestException) as error:
            flash(str(error), "error")

    return render_template(
        "signup.html",
        **template_context(),
    )


@app.route("/verify", methods=["GET", "POST"])
def verify():
    phone = session.get("pending_phone")

    if not phone:
        return redirect(url_for("signup"))

    if request.method == "POST":
        try:
            result = twilio.verify.v2.services(
                VERIFY_SID
            ).verification_checks.create(
                to=phone,
                code=request.form["code"].strip(),
            )

            if result.status == "approved":
                session["verified_signup"] = True
                return redirect(
                    url_for("create_password")
                )

            flash(
                tr("bad_code"),
                "error",
            )

        except TwilioRestException as error:
            flash(str(error), "error")

    return render_template(
        "verify.html",
        **template_context(phone=phone),
    )


@app.route("/create-password", methods=["GET", "POST"])
def create_password():
    phone = session.get("pending_phone")

    if (
        not phone
        or not session.get("verified_signup")
    ):
        return redirect(url_for("signup"))

    if request.method == "POST":
        password = request.form["password"]

        if len(password) < 8:
            flash(
                tr("password_short"),
                "error",
            )

        elif password != request.form["confirm_password"]:
            flash(
                tr("password_mismatch"),
                "error",
            )

        else:
            lang = get_language()

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO users(
                            phone_number,
                            password_hash,
                            phone_verified,
                            language
                        )
                        VALUES(%s,%s,TRUE,%s)
                        ON CONFLICT(phone_number)
                        DO UPDATE SET
                            password_hash=EXCLUDED.password_hash,
                            phone_verified=TRUE,
                            language=EXCLUDED.language
                        RETURNING id
                    """, (
                        phone,
                        generate_password_hash(password),
                        lang,
                    ))

                    uid = cur.fetchone()["id"]

            # Preserve selected language after clearing signup state.
            session.clear()
            session["user_id"] = uid
            session["lang"] = lang

            return redirect(
                url_for("dashboard")
            )

    return render_template(
        "create_password.html",
        **template_context(),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            phone = normalize_phone(
                request.form["phone_number"]
            )
        except ValueError:
            phone = ""

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM users
                    WHERE phone_number=%s
                    """,
                    (phone,),
                )
                user = cur.fetchone()

        if (
            user
            and check_password_hash(
                user["password_hash"],
                request.form["password"],
            )
        ):
            session.clear()
            session["user_id"] = user["id"]
            session["lang"] = normalize_language(
                user.get("language") or "et"
            )

            return redirect(
                url_for("dashboard")
            )

        flash(
            tr("invalid_login"),
            "error",
        )

    return render_template(
        "login.html",
        **template_context(),
    )


@app.post("/logout")
def logout():
    lang = get_language()
    session.clear()
    session["lang"] = lang
    return redirect(url_for("login"))


@app.get("/api/departures")
def departures():
    direction = request.args.get(
        "direction",
        "",
    )

    try:
        d = date.fromisoformat(
            request.args.get(
                "date",
                "",
            )
        )
    except ValueError:
        return jsonify(
            ok=False,
            error=(
                "Vigane kuupäev."
                if get_language() == "et"
                else "Invalid date."
            ),
        ), 400

    if direction not in ROUTES:
        return jsonify(
            ok=False,
            error=tr("invalid_route"),
        ), 400

    try:
        times = get_departure_times(
            direction,
            d,
        )

        return jsonify(
            ok=True,
            departures=times,
        )

    except Exception:
        app.logger.exception(
            "Departure loading failed"
        )

        return jsonify(
            ok=False,
            error=(
                "Praamid.ee väljumisi ei õnnestunud laadida."
                if get_language() == "et"
                else "Could not load Praamid.ee departures."
            ),
        ), 502


@app.route("/")
def dashboard():
    expire_old()
    user = require_user()

    # Keep DB preference and session preference aligned.
    lang = get_language()
    if user and user.get("language") != lang:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET language=%s
                    WHERE id=%s
                    """,
                    (lang, user["id"]),
                )
        user["language"] = lang

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT *
                FROM trackers
                WHERE user_id=%s
                  AND status IN ('active','paused')
                ORDER BY
                    travel_date,
                    departure_time
            """, (
                user["id"],
            ))

            trackers = cur.fetchall()

    # Always display Europe/Tallinn time.
    for tracker in trackers:
        checked = tracker.get(
            "last_checked_at"
        )

        if checked:
            if checked.tzinfo is None:
                checked = checked.replace(
                    tzinfo=UTC
                )

            tracker["last_checked_display"] = (
                checked
                .astimezone(TZ)
                .strftime("%d.%m.%Y %H:%M")
            )
        else:
            tracker["last_checked_display"] = None

    return render_template(
        "dashboard.html",
        **template_context(
            user=user,
            trackers=trackers,
            routes=ROUTES,
            today=(
                datetime.now(TZ)
                .date()
                .isoformat()
            ),
            max_open=MAX_OPEN_TRACKERS,
            can_add=(
                len(trackers)
                < MAX_OPEN_TRACKERS
            ),
        ),
    )


@app.post("/trackers")
def add_tracker():
    expire_old()
    user = require_user()

    direction = request.form["direction"]
    d = date.fromisoformat(
        request.form["travel_date"]
    )
    departure_time = request.form[
        "departure_time"
    ]

    if direction not in ROUTES:
        flash(
            tr("invalid_route"),
            "error",
        )
        return redirect(
            url_for("dashboard")
        )

    try:
        valid = get_departure_times(
            direction,
            d,
        )
    except Exception:
        flash(
            tr("schedule_fail"),
            "error",
        )
        return redirect(
            url_for("dashboard")
        )

    if departure_time not in valid:
        flash(
            tr("invalid_departure"),
            "error",
        )
        return redirect(
            url_for("dashboard")
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS n
                FROM trackers
                WHERE user_id=%s
                  AND status IN ('active','paused')
            """, (
                user["id"],
            ))

            if cur.fetchone()["n"] >= MAX_OPEN_TRACKERS:
                flash(
                    tr("max_5"),
                    "error",
                )
                return redirect(
                    url_for("dashboard")
                )

            cur.execute("""
                INSERT INTO trackers(
                    user_id,
                    direction,
                    travel_date,
                    departure_time,
                    vehicle_type,
                    status,
                    alert_sent
                )
                VALUES(
                    %s,%s,%s,%s,
                    'Sõiduauto',
                    'active',
                    FALSE
                )
                ON CONFLICT(
                    user_id,
                    direction,
                    travel_date,
                    departure_time,
                    vehicle_type
                )
                DO UPDATE SET
                    status='active',
                    alert_sent=FALSE,
                    alert_sent_at=NULL,
                    last_available=NULL,
                    last_count=NULL,
                    last_checked_at=NULL,
                    last_error=NULL
            """, (
                user["id"],
                direction,
                d,
                departure_time,
            ))

    flash(
        tr("tracker_activated"),
        "success",
    )

    return redirect(
        url_for("dashboard")
    )


@app.post("/trackers/<int:tid>/toggle")
def toggle(tid):
    user = require_user()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE trackers
                SET status=
                    CASE
                        WHEN status='active'
                        THEN 'paused'
                        ELSE 'active'
                    END
                WHERE id=%s
                  AND user_id=%s
            """, (
                tid,
                user["id"],
            ))

    return redirect(
        url_for("dashboard")
    )


@app.post("/trackers/<int:tid>/delete")
def delete(tid):
    user = require_user()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM trackers
                WHERE id=%s
                  AND user_id=%s
                """,
                (
                    tid,
                    user["id"],
                ),
            )

    flash(
        tr("tracker_removed"),
        "success",
    )

    return redirect(
        url_for("dashboard")
    )


@app.get("/health")
def health():
    return {"ok": True}
