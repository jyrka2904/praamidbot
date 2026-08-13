# Praamid Tracker — Multi-user SMS MVP

Features:
- Phone-number signup
- Twilio Verify SMS OTP
- Phone + password login
- PostgreSQL
- Private tracker queue per user
- Maximum 5 open trackers per user
- Active + paused both count toward the limit
- Expired trackers do not count
- Tracker automatically expires after its ferry departure time
- Twilio SMS availability alerts
- Separate Railway web and worker services
- Shared checking: users watching the same route/date share a Praamid page load

## Railway variables already required on web
DATABASE_URL
SECRET_KEY
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_VERIFY_SERVICE_SID

## Worker variables
DATABASE_URL
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_MESSAGING_SERVICE_SID

Optional worker variables:
CHECK_MIN_SECONDS=180
CHECK_MAX_SECONDS=240

## Web start command
gunicorn --workers 2 --threads 4 --bind 0.0.0.0:$PORT app:app

## Worker start command
python worker.py

## Deploy sequence
1. Replace the files in the GitHub repo with this package.
2. Commit to main.
3. Let the existing Railway web service deploy.
4. Generate a public domain for the web service.
5. Test signup using your own phone number.
6. Verify the Twilio SMS OTP.
7. Create a password and log in.
8. Create a second Railway service from the same GitHub repo.
9. Set its Start Command to: python worker.py
10. Add the worker variables listed above.
11. Do not generate a public domain for the worker.
12. Add a test tracker and inspect worker logs.

Important:
- Keep the old working Telegram monitor until the SMS web app has been tested.
- Twilio trial accounts may restrict SMS recipients.
