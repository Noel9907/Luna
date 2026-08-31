"""
The retention job. Run it on a timer.

    python scripts/purge.py           one pass, then exit  (for cron/systemd)
    python scripts/purge.py --loop    stay running, one pass per interval

One pass per hour is plenty: every deadline here is measured in days, so an
hour of lateness is invisible, and running it as a separate process means a
purge that fails cannot take the API down with it.

Deliberately safe to run twice. Each clock checks its own marker before doing
anything, so a cron entry that overlaps itself is harmless.
"""

from __future__ import annotations

import pathlib
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app import retention  # noqa: E402
from app.db import system_session  # noqa: E402


def one_pass() -> int:
    started = time.time()
    with system_session() as db:
        report = retention.run_all(db)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"  {stamp}  {report.summary()}  ({time.time() - started:.1f}s)")
    if report.events_touched:
        print(f"              events purged: {', '.join(report.events_touched)}")
    return 0


def main(argv: list[str]) -> int:
    if "--loop" not in argv:
        return one_pass()

    interval = retention.interval_seconds()
    print(f"  purge loop up, one pass every {interval // 60} minutes. ctrl-c to stop.")
    while True:
        try:
            one_pass()
        except Exception as exc:  # noqa: BLE001 - a bad pass must not end the loop
            print(f"  purge failed: {type(exc).__name__}: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
