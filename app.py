import os, re
from datetime import datetime, date
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from database import init_db, get_conn
from praamid import ROUTES, get_departure_times

TZ = ZoneInfo("Europe/Tallinn")
UTC = ZoneInfo("UTC")
MAX_OPEN_TRACKERS = 5

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

twilio = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
VERIFY_SID = os.environ["TWILIO_VERIFY_SERVICE_SID"]

init_db()


def normalize_phone(raw):
    value = re.sub(r"[^\d+]", "", raw or "")
    if value.startswith("00"):
        value = "+" + value[2:]
    if value.startswith("5") and not value.startswith("+"):
        value = "+372" + value
    if not re.fullmatch(r"\+\d{7,15}", value):
        raise ValueError("Use an international phone number, e.g. +37255512345")
    return value


def require_user():
    uid = session.get("user_id")
    if not uid:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE id=%s", (uid,))
            return cur.fetchone()


def expire_old():
    now = datetime.now(TZ)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM trackers
                WHERE travel_date < %s
                   OR (travel_date = %s AND departure_time <= %s)
            """, (now.date(), now.date(), now.time().replace(tzinfo=None)))


@app.before_request
def auth_guard():
    allowed = {"login", "signup", "verify", "create_password", "health", "static"}
    if request.endpoint and request.endpoint not in allowed and not session.get("user_id"):
        return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        try:
            phone = normalize_phone(request.form["phone_number"])
            twilio.verify.v2.services(VERIFY_SID).verifications.create(to=phone, channel="sms")
            session["pending_phone"] = phone
            return redirect(url_for("verify"))
        except (ValueError, TwilioRestException) as e:
            flash(str(e), "error")
    return render_template("signup.html")


@app.route("/verify", methods=["GET", "POST"])
def verify():
    phone = session.get("pending_phone")
    if not phone:
        return redirect(url_for("signup"))
    if request.method == "POST":
        try:
            result = twilio.verify.v2.services(VERIFY_SID).verification_checks.create(
                to=phone,
                code=request.form["code"].strip(),
            )
            if result.status == "approved":
                session["verified_signup"] = True
                return redirect(url_for("create_password"))
            flash("Incorrect or expired verification code.", "error")
        except TwilioRestException as e:
            flash(str(e), "error")
    return render_template("verify.html", phone=phone)


@app.route("/create-password", methods=["GET", "POST"])
def create_password():
    phone = session.get("pending_phone")
    if not phone or not session.get("verified_signup"):
        return redirect(url_for("signup"))
    if request.method == "POST":
        pw = request.form["password"]
        if len(pw) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif pw != request.form["confirm_password"]:
            flash("Passwords do not match.", "error")
        else:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO users(phone_number,password_hash,phone_verified)
                        VALUES(%s,%s,TRUE)
                        ON CONFLICT(phone_number) DO UPDATE SET
                            password_hash=EXCLUDED.password_hash,
                            phone_verified=TRUE
                        RETURNING id
                    """, (phone, generate_password_hash(pw)))
                    uid = cur.fetchone()["id"]
            session.clear()
            session["user_id"] = uid
            return redirect(url_for("dashboard"))
    return render_template("create_password.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            phone = normalize_phone(request.form["phone_number"])
        except ValueError:
            phone = ""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE phone_number=%s", (phone,))
                user = cur.fetchone()
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("dashboard"))
        flash("Invalid phone number or password.", "error")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/departures")
def departures():
    direction = request.args.get("direction", "")
    try:
        d = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return jsonify(ok=False, error="Invalid date"), 400
    if direction not in ROUTES:
        return jsonify(ok=False, error="Invalid route"), 400
    try:
        times = get_departure_times(direction, d)
        return jsonify(ok=True, departures=times)
    except Exception:
        app.logger.exception("Departure loading failed")
        return jsonify(ok=False, error="Could not load Praamid.ee departures"), 502


@app.route("/")
def dashboard():
    expire_old()
    user = require_user()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM trackers
                WHERE user_id=%s AND status IN ('active','paused')
                ORDER BY travel_date, departure_time
            """, (user["id"],))
            trackers = cur.fetchall()

    # Explicit Tallinn-time display regardless of DB/server timezone.
    for t in trackers:
        checked = t.get("last_checked_at")
        if checked:
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=UTC)
            t["last_checked_display"] = checked.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
        else:
            t["last_checked_display"] = None

    return render_template(
        "dashboard.html",
        user=user,
        trackers=trackers,
        routes=ROUTES,
        today=datetime.now(TZ).date().isoformat(),
        max_open=MAX_OPEN_TRACKERS,
        can_add=len(trackers) < MAX_OPEN_TRACKERS,
    )


@app.post("/trackers")
def add_tracker():
    expire_old()
    user = require_user()
    direction = request.form["direction"]
    d = date.fromisoformat(request.form["travel_date"])
    t = request.form["departure_time"]

    if direction not in ROUTES:
        flash("Invalid route.", "error")
        return redirect(url_for("dashboard"))

    try:
        valid = get_departure_times(direction, d)
    except Exception:
        flash("Could not verify the ferry schedule. Try again.", "error")
        return redirect(url_for("dashboard"))

    if t not in valid:
        flash("That is not a scheduled departure for the selected route/date.", "error")
        return redirect(url_for("dashboard"))

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) AS n FROM trackers
                WHERE user_id=%s AND status IN ('active','paused')
            """, (user["id"],))
            if cur.fetchone()["n"] >= MAX_OPEN_TRACKERS:
                flash("Maximum 5 open trackers per user.", "error")
                return redirect(url_for("dashboard"))

            cur.execute("""
                INSERT INTO trackers(user_id,direction,travel_date,departure_time,vehicle_type,status,alert_sent)
                VALUES(%s,%s,%s,%s,'Sõiduauto','active',FALSE)
                ON CONFLICT(user_id,direction,travel_date,departure_time,vehicle_type)
                DO UPDATE SET status='active', alert_sent=FALSE, alert_sent_at=NULL,
                              last_available=NULL,last_count=NULL,last_checked_at=NULL,last_error=NULL
            """, (user["id"], direction, d, t))

    flash("Tracker activated. We'll notify you if a passenger-car ticket becomes available.", "success")
    return redirect(url_for("dashboard"))


@app.post("/trackers/<int:tid>/toggle")
def toggle(tid):
    user = require_user()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE trackers
                SET status=CASE WHEN status='active' THEN 'paused' ELSE 'active' END
                WHERE id=%s AND user_id=%s
            """, (tid, user["id"]))
    return redirect(url_for("dashboard"))


@app.post("/trackers/<int:tid>/delete")
def delete(tid):
    user = require_user()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM trackers WHERE id=%s AND user_id=%s", (tid, user["id"]))
    flash("Tracker removed.", "success")
    return redirect(url_for("dashboard"))


@app.get("/health")
def health():
    return {"ok": True}
