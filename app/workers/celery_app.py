"""Celery application factory and configuration."""

from celery import Celery

from app.core.config import CELERY_BROKER_URL, CELERY_RESULT_BACKEND

celery_app = Celery(
    "apartment_scraper",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # Discover tasks in these modules
    include=["app.workers.tasks"],

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

    # Beat schedule — Craigslist scrape every 15 minutes
    beat_schedule={
        "scrape-craigslist-every-15m": {
            "task": "app.workers.tasks.scrape_craigslist",
            "schedule": 900.0,  # 15 minutes in seconds
        },
    },
)
