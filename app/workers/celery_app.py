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
    include=[
        "app.workers.tasks",
        "app.workers.enrichment_tasks",
        "app.workers.pricing_tasks",
        "app.workers.alert_tasks",
        "app.workers.dedup_tasks",
        "app.workers.staleness_tasks",
        "app.workers.backup_tasks",
    ],

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
        "app.workers.dedup_tasks.*": {"queue": "enrichment"},
        "app.workers.staleness_tasks.*": {"queue": "enrichment"},
        "app.workers.backup_tasks.*": {"queue": "enrichment"},
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
        # Phase 5: Multi-source scraping
        "scrape-domu-every-20m": {
            "task": "app.workers.tasks.scrape_domu",
            "schedule": 1200.0,
        },
        "poll-zillow-email-every-5m": {
            "task": "app.workers.tasks.poll_zillow_email",
            "schedule": 300.0,
        },
        "poll-apartments-email-every-5m": {
            "task": "app.workers.tasks.poll_apartments_email",
            "schedule": 300.0,
        },
        # Phase 5: Cross-source dedup
        "cross-source-dedup-every-30m": {
            "task": "app.workers.dedup_tasks.run_dedup_sweep",
            "schedule": 1800.0,
        },
        "resolve-dedup-pairs-every-30m": {
            "task": "app.workers.dedup_tasks.resolve_dedup_pairs",
            "schedule": 1800.0,
        },
        # Phase 6: Staleness detection + backups
        "mark-stale-listings-daily-3am": {
            "task": "app.workers.staleness_tasks.mark_stale_listings",
            "schedule": crontab(hour=3, minute=0),
        },
        "nightly-db-backup-2am": {
            "task": "app.workers.backup_tasks.backup_database",
            "schedule": crontab(hour=2, minute=0),
        },
    },
)
