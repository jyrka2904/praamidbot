# Praamid Tracker update

## What changed
- European displayed dates: DD.MM.YYYY
- 24-hour times: HH:MM
- Added Virtsu → Kuivastu and Kuivastu → Virtsu
- Departure dropdown is loaded from the live Praamid.ee page after route + date selection
- Server validates the departure again before saving
- Maximum 5 open trackers per user
- One SMS per availability event
- While the same ticket availability remains open, no repeat SMS is sent
- If availability disappears and later returns, the tracker re-arms and sends one new SMS for the new opening
- Past trackers are automatically deleted

## GitHub layout
Keep the HTML files inside `templates/`.

## Railway web service variables
DATABASE_URL
SECRET_KEY
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_VERIFY_SERVICE_SID

## Railway worker variables
DATABASE_URL
TWILIO_ACCOUNT_SID
TWILIO_AUTH_TOKEN
TWILIO_MESSAGING_SERVICE_SID

Optional worker variables:
CHECK_MIN_SECONDS=180
CHECK_MAX_SECONDS=240

## Railway commands
Web service uses the Dockerfile CMD automatically.

Worker service start command:
python worker.py

## Important
This implementation deliberately requires explicit availability before sending an SMS. If Praamid.ee changes its HTML, the worker should fail conservatively rather than falsely alerting.

The `alert_sent` flag represents the current availability event. It is persisted in PostgreSQL, so redeploying does not cause duplicate alerts. When availability returns to zero, the worker resets the flag and waits for the next new opening.
