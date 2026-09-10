"""Zero-dependency fallback scheduler (stdlib only) for when Prefect isn't
running — e.g. a dev laptop. Fires the same tier bundles on the same cadence:

    python -m orchestration.scheduler

daily 06:00 · weekly Monday 06:30 · quarterly 1st of Jan/Apr/Jul/Oct 05:00.
Missed windows (machine asleep) fire on the next wake check rather than being
skipped entirely: each tier tracks its last run date.
"""
from __future__ import annotations

import time
from datetime import date, datetime

from orchestration.pipelines import refresh

_CHECK_EVERY_S = 300


def _due(tier: str, now: datetime, last: dict[str, date]) -> bool:
    if last.get(tier) == now.date():
        return False
    if tier == "daily":
        return now.hour >= 6
    if tier == "weekly":
        return now.weekday() == 0 and (now.hour, now.minute) >= (6, 30)
    if tier == "quarterly":
        return now.month in (1, 4, 7, 10) and now.day == 1 and now.hour >= 5
    return False


def main() -> None:
    last_run: dict[str, date] = {}
    print("scheduler up: daily 06:00 | weekly Mon 06:30 | quarterly 1st 05:00", flush=True)
    while True:
        now = datetime.now()
        for tier in ("quarterly", "weekly", "daily"):  # coarsest first
            if _due(tier, now, last_run):
                try:
                    refresh(tier)
                except Exception as exc:  # keep the loop alive
                    print(f"refresh({tier}) failed: {exc}", flush=True)
                last_run[tier] = now.date()
        time.sleep(_CHECK_EVERY_S)


if __name__ == "__main__":
    main()
