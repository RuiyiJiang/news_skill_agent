from __future__ import annotations

import logging
from collections.abc import Sequence

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Settings
from app.pipeline import run_pipeline


LOGGER = logging.getLogger(__name__)


def start_scheduler(settings: Settings, selected_groups: Sequence[str] | None = None) -> None:
    scheduler = BlockingScheduler(timezone=settings.app_timezone)
    trigger = CronTrigger(
        hour=settings.schedule_hour,
        minute=settings.schedule_minute,
        timezone=settings.app_timezone,
    )

    scheduler.add_job(
        func=run_pipeline,
        trigger=trigger,
        kwargs={"settings": settings, "selected_groups": list(selected_groups or [])},
        id="daily_news_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    LOGGER.info(
        "Scheduler started. timezone=%s schedule=%02d:%02d groups=%s",
        settings.app_timezone,
        settings.schedule_hour,
        settings.schedule_minute,
        ", ".join(selected_groups or []) or "ALL",
    )
    scheduler.start()
