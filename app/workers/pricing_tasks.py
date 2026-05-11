"""Celery tasks for price distribution rebuilds and deal scoring."""

from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.models.enrichment import EnrichmentResult
from app.models.listing import Listing
from app.services.pricing import PricingService
from app.workers.celery_app import celery_app

setup_logging()
logger = get_logger(__name__)


@celery_app.task(
    name="app.workers.pricing_tasks.rebuild_price_distributions",
    bind=True,
    max_retries=2,
    queue="enrichment",
)
def rebuild_price_distributions(self) -> dict:
    """Rebuild rolling price distributions for all neighborhood segments."""
    logger.info("rebuild_price_distributions_started")

    db = SessionLocal()
    try:
        result = PricingService().rebuild_distributions(db)
        logger.info("rebuild_price_distributions_complete", **result)
        return result
    except Exception as exc:
        logger.error("rebuild_price_distributions_failed", error=str(exc))
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(
    name="app.workers.pricing_tasks.score_new_listing",
    bind=True,
    max_retries=2,
    queue="enrichment",
)
def score_new_listing(self, listing_id: str) -> dict:
    """Compute deal_score for a single listing after enrichment."""
    logger.info("score_new_listing_started", listing_id=listing_id)

    db = SessionLocal()
    try:
        listing = db.query(Listing).filter(Listing.id == listing_id).first()
        if not listing:
            logger.warning("listing_not_found", listing_id=listing_id)
            return {"status": "skipped", "reason": "not_found"}

        enrichment = (
            db.query(EnrichmentResult)
            .filter(EnrichmentResult.listing_id == listing.id)
            .first()
        )
        if not enrichment:
            return {"status": "skipped", "reason": "no_enrichment"}
        if enrichment.skipped:
            return {"status": "skipped", "reason": "pre_filtered"}
        if enrichment.failed:
            return {"status": "skipped", "reason": "enrichment_failed"}

        score = PricingService().score_listing(db, listing)
        listing.deal_score = score
        db.commit()

        logger.info(
            "score_new_listing_complete",
            listing_id=listing_id,
            deal_score=score,
            gated=score is None,
        )
        return {"status": "scored", "deal_score": score}

    except Exception as exc:
        logger.error("score_new_listing_failed", listing_id=listing_id, error=str(exc))
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


@celery_app.task(
    name="app.workers.pricing_tasks.backfill_deal_scores",
    bind=True,
    queue="enrichment",
)
def backfill_deal_scores(self) -> dict:
    """Find enriched (not skipped/failed) listings missing deal_score and dispatch scoring."""
    logger.info("backfill_deal_scores_started")

    db = SessionLocal()
    try:
        missing = (
            db.query(Listing.id)
            .join(EnrichmentResult, Listing.id == EnrichmentResult.listing_id)
            .filter(
                EnrichmentResult.skipped.is_(False),
                EnrichmentResult.failed.is_(False),
                Listing.deal_score.is_(None),
            )
            .limit(100)
            .all()
        )

        dispatched = 0
        for (listing_id,) in missing:
            score_new_listing.delay(str(listing_id))
            dispatched += 1

        logger.info("backfill_deal_scores_complete", dispatched=dispatched)
        return {"dispatched": dispatched}
    finally:
        db.close()
