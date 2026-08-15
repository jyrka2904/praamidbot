# Praamid Tracker — professional update

Changes:
- Last checked time is explicitly displayed in Europe/Tallinn time.
- Randomized worker interval is 2–3 minutes.
- When availability is detected, the SMS is submitted to Twilio and that exact tracker is removed from PostgreSQL.
- The SMS tells the user the tracker has been removed and to add it again if they miss the ticket.
- If the Twilio API call itself fails, the tracker is kept and retried later.
- More polished responsive UI.
- Maximum 5 open trackers per user remains.
- Past departures are automatically removed.

IMPORTANT: If Railway already has interval variables, set:
CHECK_MIN_SECONDS=120
CHECK_MAX_SECONDS=180

Worker start command:
python worker.py
