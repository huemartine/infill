"""Prefect flow wrappers over the plain pipelines (Section 3: Prefect for MVP).

Serve all schedules from one long-running process:

    python -m orchestration.flows

Cron (server local time): violations daily 06:00; sheriff sales Mondays 06:30
(sales run Wednesdays — Monday's scrape catches the docket with lead time);
CAGIS quarterly on the 1st at 05:00. Prefect gives retries, run history, and a
UI when pointed at a Prefect API; the pipelines themselves stay orchestrator-
agnostic (see scheduler.py for the no-dependency fallback).
"""
from __future__ import annotations

from prefect import flow

from orchestration.pipelines import refresh


@flow(name="infill-daily", retries=1, retry_delay_seconds=300)
def daily() -> dict:
    return refresh("daily")


@flow(name="infill-weekly", retries=1, retry_delay_seconds=600)
def weekly() -> dict:
    return refresh("weekly")


@flow(name="infill-quarterly", retries=1, retry_delay_seconds=3600)
def quarterly() -> dict:
    return refresh("quarterly")


if __name__ == "__main__":
    from prefect import serve

    serve(
        daily.to_deployment(name="daily-0600", cron="0 6 * * *"),
        weekly.to_deployment(name="weekly-mon-0630", cron="30 6 * * 1"),
        quarterly.to_deployment(name="quarterly-1st-0500", cron="0 5 1 */3 *"),
    )
