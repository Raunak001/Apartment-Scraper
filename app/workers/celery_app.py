"""Celery application factory and configuration."""

from celery import Celery
from celery.schedules import crontab

from app.core.config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND

celery_app = Celery(
    "apartment_scraper",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # Discover tasks in these modules
    include=["app.workers.tasks", "app.workers.enrichment_tasks", "app.workers.pricing_tasks", "app.workers.alert_tasks"],

    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="America/Chicago",
    enable_utc=True,

    # Reliability: acknowledge tasks only after they complete
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Queue routing
    task_routes={
        "app.workers.tasks.*": {"queue": "scraping"},
        "app.workers.enrichment_tasks.*": {"queue": "enrichment"},
        "app.workers.pricing_tasks.*": {"queue": "enrichment"},
        "app.workers.alert_tasks.*": {"queue": "enrichment"},
    },

    # Beat schedule
    beat_schedule={
        "scrape-craigslist-every-15m": {
            "task": "app.workers.tasks.scrape_craigslist",
            "schedule": 900.0,
        },
        "backfill-unenriched-every-5m": {
            "task": "app.workers.enrichment_tasks.backfill_unenriched",
            "schedule": 300.0,
        },
        "rebuild-distributions-every-30m": {
            "task": "app.workers.pricing_tasks.rebuild_price_distributions",
            "schedule": 1800.0,
        },
        "backfill-deal-scores-every-10m": {
            "task": "app.workers.pricing_tasks.backfill_deal_scores",
            "schedule": 600.0,
        },
        "backfill-composite-scores-every-10m": {
            "task": "app.workers.alert_tasks.backfill_composite_scores",
            "schedule": 600.0,
        },
        "dispatch-alerts-every-10m": {
            "task": "app.workers.alert_tasks.dispatch_alerts",
            "schedule": 600.0,
        },
        "daily-heartbeat-9am": {
            "task": "app.workers.alert_tasks.daily_heartbeat",
            "schedule": crontab(hour=9, minute=0),
        },
    },
)
