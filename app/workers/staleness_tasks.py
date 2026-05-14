"""Celery tasks for listing staleness detection and lifecycle management."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.models.listing import Listing
from app.models.preference import Preference
from app.workers.celery_app import celery_app

setup_logging()
logger = get_logger(__name__)

DEFAULT_TTL_DAYS = 14


@celery_app.task(
    name="app.workers.staleness_tasks.mark_stale_listings",
    bind=True,
    max_retries=2,
)
def mark_stale_listings(self) -> dict:
    """Mark listings as stale if not re-seen within the TTL window.

    Uses COALESCE(last_checked_at, scraped_at) as the effective timestamp.
    Only transitions active -> stale.
    """
    db = SessionLocal()
    try:
        pref = db.query(Preference).filter(Preference.active.is_(True)).first()
        ttl_days = DEFAULT_TTL_DAYS
        if pref and pref.config:
            ttl_days = pref.config.get("staleness", {}).get("ttl_days", DEFAULT_TTL_DAYS)

        cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
        effective_check = func.coalesce(Listing.last_checked_at, Listing.scraped_at)

        stale_ids = (
            db.execute(
                select(Listing.id).where(
                    Listing.status == "active",
                    effective_check < cutoff,
                )
            )
            .scalars()
            .all()
        )

        if stale_ids:
            db.execute(
                update(Listing)
                .where(Listing.id.in_(stale_ids))
                .values(status="stale")
            )
            db.commit()

        logger.info(
            "mark_stale_listings_complete",
            marked_stale=len(stale_ids),
            ttl_days=ttl_days,
            cutoff=cutoff.isoformat(),
        )
        return {"marked_stale": len(stale_ids), "ttl_days": ttl_days}

    except Exception as exc:
        db.rollback()
        logger.error("mark_stale_listings_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
