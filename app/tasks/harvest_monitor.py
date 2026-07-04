"""Harvest monitor task — polls growing farms and nudges farmers for readiness.

Runs every 6 hours (see beat schedule in celery_app). Delegates to the
Logistics Agent's ``monitor_active_harvests``.
"""

from __future__ import annotations

from typing import Any

from app.agents.logistics_agent import LogisticsAgent
from app.tasks.celery_app import celery_app
from app.tasks.runner import run_task
from app.utils.logger import get_logger

log = get_logger("yieldra.task.harvest_monitor")


@celery_app.task(name="yieldra.monitor_harvests")
def monitor_harvests() -> dict[str, Any]:
    """Check all growing farms; message farmers for a status update."""
    agent = LogisticsAgent()
    result = run_task(lambda s: agent.monitor_active_harvests(s))
    log.info("monitor_harvests result=%s", result)
    return result
