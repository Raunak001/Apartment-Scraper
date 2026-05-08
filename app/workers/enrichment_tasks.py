"""Celery tasks for LLM enrichment pipeline."""

from datetime import datetime, timezone

import anthropic
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import ANTHROPIC_API_KEY, load_preferences
from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.models.enrichment import EnrichmentResult
from app.models.listing import Listing
from app.models.preference import Preference
from app.services.enrichment import EnrichmentService
from app.workers.celery_app import celery_app

setup_logging()
logger = get_logger(__name__)


def _get_active_preferences(db: Session) -> dict | None:
    pref = db.query(Preference).filter(Preference.active.is_(True)).first()
    if pref:
        return pref.config
    return None


def _listing_outside_preference_range(listing: Listing, preferences: dict) -> bool:
    pricing = preferences.get("pricing", {})
    unit = preferences.get("unit", {})

    max_price = pricing.get("max_price")
    if max_price and listing.price and listing.price > max_price:
        return True

    min_bedrooms = unit.get("min_bedrooms")
    max_bedrooms = unit.get("max_bedrooms")
    if listing.bedrooms is not None:
        if min_bedrooms is not None and listing.bedrooms < min_bedrooms:
            return True
        if max_bedrooms is not None and listing.bedrooms > max_bedrooms:
            return True

    return False


@celery_app.task(
    name="app.workers.enrichment_tasks.enrich_listing",
    bind=True,
    max_retries=3,
    autoretry_for=(anthropic.InternalServerError, anthropic.APITimeoutError, anthropic.RateLimitError),
    retry_backoff=30,
    retry_backoff_max=120,
    retry_jitter=True,
    queue="enrichment",
    rate_limit="100/m",
)
def enrich_listing(self, listing_id: str) -> dict:
    """Enrich a single listing with LLM-extracted amenities and preference score."""
    logger.info("enrich_listing_started", listing_id=listing_id)

    if not ANTHROPIC_API_KEY:
        logger.error("anthropic_api_key_missing")
        return {"status": "error", "reason": "no_api_key"}

    db = SessionLocal()
    try:
        listing = db.query(Listing).filter(Listing.id == listing_id).first()
        if not listing:
            logger.warning("listing_not_found", listing_id=listing_id)
            return {"status": "skipped", "reason": "not_found"}

        if listing.status == "enrichment_failed":
            return {"status": "skipped", "reason": "previously_failed"}

        existing = (
            db.query(EnrichmentResult)
            .filter(EnrichmentResult.listing_id == listing.id)
            .first()
        )
        if existing:
            return {"status": "skipped", "reason": "already_enriched"}

        preferences = _get_active_preferences(db)
        if not preferences:
            logger.error("no_active_preferences")
            return {"status": "error", "reason": "no_preferences"}

        # Pre-filter: skip LLM calls for obvious mismatches
        if _listing_outside_preference_range(listing, preferences):
            enrichment = EnrichmentResult(
                listing_id=listing.id,
                amenities=None,
                preference_score=0.0,
                llm_notes="skipped:out_of_preference_range",
                tokens_used=0,
                enriched_at=datetime.now(timezone.utc),
                skipped=True,
                failed=False,
            )
            db.add(enrichment)
            db.commit()
            logger.info(
                "enrich_listing_skipped_filter",
                listing_id=listing_id,
                price=listing.price,
                bedrooms=listing.bedrooms,
            )
            return {"status": "skipped", "reason": "out_of_range"}

        # Run LLM enrichment
        service = EnrichmentService()
        output = service.enrich(listing, preferences)

        enrichment = EnrichmentResult(
            listing_id=listing.id,
            amenities=output.amenities,
            preference_score=output.preference_score,
            llm_notes=output.llm_notes,
            tokens_used=output.tokens_used,
            enriched_at=datetime.now(timezone.utc),
            skipped=False,
            failed=False,
        )
        db.add(enrichment)
        db.commit()

        logger.info(
            "enrich_listing_complete",
            listing_id=listing_id,
            preference_score=output.preference_score,
            tokens_used=output.tokens_used,
        )
        return {
            "status": "enriched",
            "preference_score": output.preference_score,
            "tokens_used": output.tokens_used,
        }

    except (anthropic.APIStatusError, anthropic.APITimeoutError):
        raise
    except ValueError as e:
        # JSON parse failure from LLM — retry once, then mark failed
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30)
        logger.error("enrich_listing_parse_failure", listing_id=listing_id, error=str(e))
        enrichment = EnrichmentResult(
            listing_id=listing.id,
            amenities=None,
            preference_score=None,
            llm_notes="parse_failure",
            tokens_used=0,
            enriched_at=datetime.now(timezone.utc),
            skipped=False,
            failed=True,
        )
        db.add(enrichment)
        listing.status = "enrichment_failed"
        db.commit()
        return {"status": "failed", "reason": "parse_failure"}
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error("enrich_listing_permanent_failure", listing_id=listing_id, error=str(exc))
            listing = db.query(Listing).filter(Listing.id == listing_id).first()
            if listing:
                listing.status = "enrichment_failed"
                db.commit()
            return {"status": "failed", "reason": str(exc)}
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(
    name="app.workers.enrichment_tasks.backfill_unenriched",
    bind=True,
    queue="enrichment",
)
def backfill_unenriched(self) -> dict:
    """Poll for listings missing enrichment results and dispatch enrichment tasks."""
    logger.info("backfill_unenriched_started")

    db = SessionLocal()
    try:
        unenriched = (
            db.query(Listing.id)
            .outerjoin(EnrichmentResult, Listing.id == EnrichmentResult.listing_id)
            .filter(EnrichmentResult.id.is_(None))
            .filter(Listing.status != "enrichment_failed")
            .limit(50)
            .all()
        )

        dispatched = 0
        for (listing_id,) in unenriched:
            enrich_listing.delay(str(listing_id))
            dispatched += 1

        logger.info("backfill_unenriched_complete", dispatched=dispatched)
        return {"dispatched": dispatched}

    finally:
        db.close()
