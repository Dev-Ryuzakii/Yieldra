"""Celery application instance + beat schedule for autonomous agent tasks."""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "yieldra",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.harvest_monitor",
        "app.tasks.payout_distributor",
        "app.tasks.report_scheduler",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Africa/Lagos",
    enable_utc=True,
    beat_schedule={
        # Nudge farmers of growing farms every 6 hours.
        "monitor-harvests-6h": {
            "task": "yieldra.monitor_harvests",
            "schedule": crontab(minute=0, hour="*/6"),
        },
        # Distribute returns for sold harvests daily at 09:00 WAT.
        "distribute-payouts-daily": {
            "task": "yieldra.distribute_completed_payouts",
            "schedule": crontab(minute=0, hour=9),
        },
        # Weekly investor portfolio reports, Mondays 08:00 WAT.
        "weekly-investor-reports": {
            "task": "yieldra.send_weekly_investor_reports",
            "schedule": crontab(minute=0, hour=8, day_of_week=1),
        },
    },
)
