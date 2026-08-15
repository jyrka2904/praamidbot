# Praamid.ee Tracker bilingual update

Default language: Estonian (`et`).

Users can switch between ET and EN. The selected language:
- changes the UI,
- is stored in `users.language`,
- persists after login,
- controls the availability SMS language.

Important: `praamid.py` is the already-working scraper and was not rewritten for this update.

Replace these files in GitHub:
- app.py
- database.py
- worker.py
- templates/dashboard.html
- templates/login.html
- templates/signup.html
- templates/verify.html
- templates/create_password.html

You can also upload the full package, including the unchanged working `praamid.py`.

Worker variables remain:
CHECK_MIN_SECONDS=120
CHECK_MAX_SECONDS=180
