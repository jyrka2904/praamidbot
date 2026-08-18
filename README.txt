Praamid.ee Tracker – shared-browser worker update

Replace/add these files in the SAME GitHub repository root:
1. worker.py       -> replace existing worker.py
2. praamid.py      -> replace existing praamid.py
3. check_cycle.py  -> NEW file

You can leave check_once.py in the repository; this worker no longer uses it.

Railway worker start command stays:
    python worker.py

Recommended Railway variables:
    CHECK_MIN_SECONDS=5
    CHECK_MAX_SECONDS=10
    CYCLE_CHECK_TIMEOUT_SECONDS=120
    MAX_CYCLES_BEFORE_REFRESH=600

If CHECK_MIN_SECONDS / CHECK_MAX_SECONDS already exist in Railway, their values
override the defaults in worker.py.

Important:
- Serverless should remain OFF for the background worker.
- Restart Policy can remain "On Failure"; preventive/resource restarts exit with
  a non-zero code so Railway will restart the service.
- This version launches Chromium ONCE per full cycle, checks all active trackers
  with fresh browser contexts/pages, closes Chromium, then waits 5–10 seconds.
- The whole cycle runs in its own process group. If it hangs, the worker kills
  that group, including Chromium descendants.
