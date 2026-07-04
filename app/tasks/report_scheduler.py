"""Weekly investor report task.

Runs every Monday 08:00 WAT (see beat schedule). Delegates to the Report Agent.
"""

from __future__ import annotations

from typing import Any

from app.agents.report_agent import ReportAgent
from app.tasks.celery_app import celery_app
from app.tasks.runner import run_task
from app.utils.logger import get_logger

log = get_logger("yieldra.task.report_scheduler")


@celery_app.task(name="yieldra.send_weekly_investor_reports")
def send_weekly_investor_reports() -> dict[str, Any]:
    agent = ReportAgent()
    result = run_task(lambda s: agent.send_weekly_reports(s))
    log.info("send_weekly_investor_reports result=%s", result)
    return result
