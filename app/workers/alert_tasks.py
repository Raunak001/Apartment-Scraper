"""Celery tasks for alert dispatching, composite scoring, and heartbeat."""

from sqlalchemy.orm import Session

from app.core.config import get_notification_service
from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.models.preference import Preference
from app.services.alerting import AlertDispatcher
from app.workers.celery_app import celery_app

setup_logging()
logger = get_logger(__name__)


def _get_active_preferences(db: Session) -> dict | None:
    pref = db.query(Preference).filter(Preference.active.is_(True)).first()
    return pref.config if pref else None


@celery_app.task(
    name="app.workers.alert_tasks.backfill_composite_scores",
    bind=True,
    max_retries=2,
    queue="enrichment",
)
def backfill_composite_scores(self) -> dict:
    """Compute composite_score for all listings with deal_score + preference_score."""
    logger.info("backfill_composite_scores_started")

    db = SessionLocal()
    try:
        preferences = _get_active_preferences(db)
        if not preferences:
            logger.error("no_active_preferences")
            return {"status": "error", "reason": "no_preferences"}

        weights = preferences.get("scoring", {}).get("weights", {"deal": 0.6, "preference": 0.4})
        result = AlertDispatcher().backfill_composite_scores(db, weights)
        logger.info("backfill_composite_scores_complete", **result)
        return result
    except Exception as exc:
        logger.error("backfill_composite_scores_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(
    name="app.workers.alert_tasks.dispatch_alerts",
    bind=True,
    max_retries=2,
    queue="enrichment",
)
def dispatch_alerts(self) -> dict:
    """Check for alert-worthy listings and fire notifications."""
    logger.info("dispatch_alerts_started")

    db = SessionLocal()
    try:
        preferences = _get_active_preferences(db)
        if not preferences:
            logger.error("no_active_preferences")
            return {"status": "error", "reason": "no_preferences"}

        result = AlertDispatcher().dispatch(db, preferences)
        logger.info("dispatch_alerts_complete", **result)
        return result
    except Exception as exc:
        logger.error("dispatch_alerts_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(
    name="app.workers.alert_tasks.evaluate_single_listing",
    bind=True,
    max_retries=2,
    queue="enrichment",
)
def evaluate_single_listing(self, listing_id: str) -> dict:
    """Evaluate a single listing for alert eligibility after scoring."""
    logger.info("evaluate_single_listing_started", listing_id=listing_id)

    db = SessionLocal()
    try:
        preferences = _get_active_preferences(db)
        if not preferences:
            return {"status": "error", "reason": "no_preferences"}

        result = AlertDispatcher().evaluate_single(db, listing_id, preferences)
        logger.info("evaluate_single_listing_complete", listing_id=listing_id, **result)
        return result
    except Exception as exc:
        logger.error("evaluate_single_listing_failed", listing_id=listing_id, error=str(exc))
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


@celery_app.task(
    name="app.workers.alert_tasks.daily_heartbeat",
    bind=True,
    max_retries=2,
    queue="enrichment",
)
def daily_heartbeat(self) -> dict:
    """Send daily heartbeat summary to Discord."""
    logger.info("daily_heartbeat_started")

    db = SessionLocal()
    try:
        stats = AlertDispatcher().gather_heartbeat_stats(db)
        service = get_notification_service()
        success = service.send_heartbeat(stats)

        status = "sent" if success else "failed"
        logger.info("daily_heartbeat_complete", status=status, **stats)
        return {"status": status, "stats": stats}
    except Exception as exc:
        logger.error("daily_heartbeat_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
