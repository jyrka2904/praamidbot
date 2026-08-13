import os
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from twilio.rest import Client
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_conn, init_db


TZ = ZoneInfo("Europe/Tallinn")
MAX_OPEN_TRACKERS = 5

SECRET_KEY = os.environ["SECRET_KEY"]
TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_VERIFY_SERVICE_SID = os.environ["TWILIO_VERIFY_SERVICE_SID"]

ROUTES = {
    "RH": "Rohuküla → Heltermaa",
    "HR": "Heltermaa → Rohuküla",
}

VEHICLES = {
    "Sõiduauto": "Passenger car",
}

app = Flask(__name__)
app.secret_key = SECRET_KEY

twilio = Client(
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
)

init_db()


def normalize_phone(raw):
    value = re.sub(r"[^\d+]", "", raw or "")

    if value.startswith("00"):
        value = "+" + value[2:]

    if value.startswith("5") and len(value) >= 7:
        value = "+372" + value

    if not value.startswith("+"):
        raise ValueError(
            "Use an international phone number, e.g. +37255512345."
        )

    if not re.fullmatch(r"\+\d{7,15}", value):
        raise ValueError("Invalid phone number.")

    return value


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE id = %s",
                (user_id,),
            )
            return cur.fetchone()


def expire_past_trackers_for_user(user_id):
    now = datetime.now(TZ)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET status = 'expired'
                WHERE user_id = %s
                  AND status IN ('active', 'paused')
                  AND (
                    travel_date < %s
                    OR (
                        travel_date = %s
                        AND departure_time <= %s
                    )
                  )
                """,
                (
                    user_id,
                    now.date(),
                    now.date(),
                    now.time().replace(tzinfo=None),
                ),
            )


def count_open_trackers(user_id):
    expire_past_trackers_for_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS count
                FROM trackers
                WHERE user_id = %s
                  AND status IN ('active', 'paused')
                """,
                (user_id,),
            )
            return cur.fetchone()["count"]


@app.before_request
def protect_pages():
    public = {
        "login",
        "signup",
        "verify",
        "create_password",
        "health",
        "static",
    }

    if request.endpoint in public:
        return

    if not session.get("user_id"):
        return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        try:
            phone = normalize_phone(
                request.form.get("phone_number")
            )
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("signup"))

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM users WHERE phone_number = %s",
                    (phone,),
                )
                if cur.fetchone():
                    flash(
                        "An account already exists for this phone number.",
                        "error",
                    )
                    return redirect(url_for("login"))

        twilio.verify.v2.services(
            TWILIO_VERIFY_SERVICE_SID
        ).verifications.create(
            to=phone,
            channel="sms",
        )

        session["pending_phone"] = phone

        flash(
            "Verification code sent by SMS.",
            "success",
        )

        return redirect(url_for("verify"))

    return render_template("signup.html")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    phone = session.get("pending_phone")

    if not phone:
        return redirect(url_for("signup"))

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()

        result = twilio.verify.v2.services(
            TWILIO_VERIFY_SERVICE_SID
        ).verification_checks.create(
            to=phone,
            code=code,
        )

        if result.status != "approved":
            flash(
                "The verification code was incorrect or expired.",
                "error",
            )
            return redirect(url_for("verify"))

        session["phone_verified_for_signup"] = True
        return redirect(url_for("create_password"))

    return render_template(
        "verify.html",
        phone=phone,
    )


@app.route("/create-password", methods=["GET", "POST"])
def create_password():
    phone = session.get("pending_phone")

    if (
        not phone
        or not session.get("phone_verified_for_signup")
    ):
        return redirect(url_for("signup"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if len(password) < 8:
            flash(
                "Password must contain at least 8 characters.",
                "error",
            )
            return redirect(url_for("create_password"))

        if password != confirm:
            flash(
                "Passwords do not match.",
                "error",
            )
            return redirect(url_for("create_password"))

        password_hash = generate_password_hash(password)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users
                    (phone_number, password_hash, phone_verified)
                    VALUES (%s, %s, TRUE)
                    RETURNING id
                    """,
                    (
                        phone,
                        password_hash,
                    ),
                )

                user_id = cur.fetchone()["id"]

        session.clear()
        session["user_id"] = user_id

        return redirect(url_for("dashboard"))

    return render_template(
        "create_password.html"
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            phone = normalize_phone(
                request.form.get("phone_number")
            )
        except ValueError:
            flash(
                "Invalid phone number or password.",
                "error",
            )
            return redirect(url_for("login"))

        password = request.form.get("password") or ""

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT *
                    FROM users
                    WHERE phone_number = %s
                    """,
                    (phone,),
                )

                user = cur.fetchone()

        if (
            not user
            or not check_password_hash(
                user["password_hash"],
                password,
            )
        ):
            flash(
                "Invalid phone number or password.",
                "error",
            )
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user["id"]

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def dashboard():
    user = current_user()

    expire_past_trackers_for_user(user["id"])

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM trackers
                WHERE user_id = %s
                  AND status IN ('active', 'paused')
                ORDER BY travel_date, departure_time
                """,
                (user["id"],),
            )

            trackers = cur.fetchall()

    open_count = len(trackers)

    return render_template(
        "dashboard.html",
        user=user,
        trackers=trackers,
        routes=ROUTES,
        vehicles=VEHICLES,
        today=datetime.now(TZ).date().isoformat(),
        max_open_trackers=MAX_OPEN_TRACKERS,
        open_count=open_count,
        can_add_tracker=(open_count < MAX_OPEN_TRACKERS),
    )


@app.post("/trackers")
def add_tracker():
    user = current_user()

    direction = request.form.get("direction")
    travel_date = request.form.get("travel_date")
    departure_time = request.form.get("departure_time")
    vehicle_type = request.form.get("vehicle_type")

    if direction not in ROUTES:
        flash("Invalid route.", "error")
        return redirect(url_for("dashboard"))

    if vehicle_type not in VEHICLES:
        flash("Invalid vehicle type.", "error")
        return redirect(url_for("dashboard"))

    try:
        target_date = date.fromisoformat(travel_date)
        target_time = datetime.strptime(
            departure_time,
            "%H:%M",
        ).time()
    except Exception:
        flash("Invalid date or departure time.", "error")
        return redirect(url_for("dashboard"))

    target_dt = datetime.combine(
        target_date,
        target_time,
        tzinfo=TZ,
    )

    if target_dt <= datetime.now(TZ):
        flash(
            "That ferry departure has already passed.",
            "error",
        )
        return redirect(url_for("dashboard"))

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Check whether this exact tracker already exists.
            cur.execute(
                """
                SELECT id, status
                FROM trackers
                WHERE user_id = %s
                  AND direction = %s
                  AND travel_date = %s
                  AND departure_time = %s
                  AND vehicle_type = %s
                """,
                (
                    user["id"],
                    direction,
                    travel_date,
                    departure_time,
                    vehicle_type,
                ),
            )
            existing = cur.fetchone()

    # Reactivating the exact same expired/deleted-equivalent tracker
    # should still respect the user's five-open-tracker limit.
    if existing and existing["status"] in ("active", "paused"):
        flash(
            "You are already tracking this ferry.",
            "error",
        )
        return redirect(url_for("dashboard"))

    open_count = count_open_trackers(user["id"])

    if open_count >= MAX_OPEN_TRACKERS:
        flash(
            f"You can have a maximum of {MAX_OPEN_TRACKERS} open trackers. "
            "Pause/delete one or wait for an existing tracker to expire.",
            "error",
        )
        return redirect(url_for("dashboard"))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trackers
                (
                    user_id,
                    direction,
                    travel_date,
                    departure_time,
                    vehicle_type,
                    status
                )
                VALUES (%s, %s, %s, %s, %s, 'active')
                ON CONFLICT (
                    user_id,
                    direction,
                    travel_date,
                    departure_time,
                    vehicle_type
                )
                DO UPDATE
                SET
                    status = 'active',
                    last_available = NULL,
                    last_count = NULL,
                    last_checked_at = NULL,
                    last_error = NULL
                """,
                (
                    user["id"],
                    direction,
                    travel_date,
                    departure_time,
                    vehicle_type,
                ),
            )

    flash("Tracker added.", "success")
    return redirect(url_for("dashboard"))


@app.post("/trackers/<int:tracker_id>/toggle")
def toggle_tracker(tracker_id):
    user = current_user()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trackers
                SET status = CASE
                    WHEN status = 'active' THEN 'paused'
                    ELSE 'active'
                END
                WHERE id = %s
                  AND user_id = %s
                  AND status IN ('active', 'paused')
                """,
                (
                    tracker_id,
                    user["id"],
                ),
            )

    return redirect(url_for("dashboard"))


@app.post("/trackers/<int:tracker_id>/delete")
def delete_tracker(tracker_id):
    user = current_user()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM trackers
                WHERE id = %s
                  AND user_id = %s
                """,
                (
                    tracker_id,
                    user["id"],
                ),
            )

    flash("Tracker deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/health")
def health():
    return {"ok": True}
