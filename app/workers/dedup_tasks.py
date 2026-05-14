"""Celery tasks for cross-source deduplication."""

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.models.dedup import DedupPair
from app.models.listing import Listing
from app.services.dedup import cross_source_dedup_sweep
from app.services.llm_dedup import MAX_PAIRS_PER_RUN, resolve_pair
from app.workers.celery_app import celery_app

setup_logging()
logger = get_logger(__name__)


@celery_app.task(name="app.workers.dedup_tasks.run_dedup_sweep", bind=True, max_retries=2)
def run_dedup_sweep(self) -> dict:
    """Run Stage-1 fuzzy dedup sweep across recent active listings."""
    logger.info("task_started", task="run_dedup_sweep")

    db = SessionLocal()
    try:
        result = cross_source_dedup_sweep(db)
        logger.info("task_complete", task="run_dedup_sweep", **result)
        return result

    except Exception as exc:
        logger.error("task_failed", task="run_dedup_sweep", error=str(exc))
        raise self.retry(exc=exc, countdown=120)

    finally:
        db.close()


@celery_app.task(name="app.workers.dedup_tasks.resolve_dedup_pairs", bind=True, max_retries=2)
def resolve_dedup_pairs(self) -> dict:
    """Run Stage-2 LLM dedup on pending DedupPair entries."""
    logger.info("task_started", task="resolve_dedup_pairs")

    db = SessionLocal()
    try:
        pending_pairs = db.execute(
            select(DedupPair)
            .where(DedupPair.status == "pending")
            .order_by(DedupPair.created_at)
            .limit(MAX_PAIRS_PER_RUN)
        ).scalars().all()

        if not pending_pairs:
            logger.info("no_pending_pairs")
            return {"resolved": 0, "not_duplicate": 0, "merged": 0, "low_confidence": 0}

        resolved = 0
        not_duplicate = 0
        merged = 0
        low_confidence = 0

        for pair in pending_pairs:
            listing_a = db.get(Listing, pair.listing_a_id)
            listing_b = db.get(Listing, pair.listing_b_id)

            if not listing_a or not listing_b:
                pair.status = "dismissed"
                pair.resolution = "listing_deleted"
                pair.resolved_at = datetime.now(timezone.utc)
                resolved += 1
                continue

            result = resolve_pair(listing_a, listing_b)

            is_dup = result.get("is_duplicate", False)
            confidence = result.get("confidence", 0.0)

            if is_dup and confidence >= 0.75:
                # Auto-merge: keep cheaper listing
                if (listing_a.price or float("inf")) <= (listing_b.price or float("inf")):
                    listing_b.status = "dedup_duplicate"
                    pair.resolution = f"merged_kept_{listing_a.id}"
                else:
                    listing_a.status = "dedup_duplicate"
                    pair.resolution = f"merged_kept_{listing_b.id}"

                pair.status = "llm_resolved"
                pair.resolved_at = datetime.now(timezone.utc)
                merged += 1
                resolved += 1

            elif not is_dup and confidence >= 0.75:
                pair.status = "llm_resolved"
                pair.resolution = "not_duplicate"
                pair.resolved_at = datetime.now(timezone.utc)
                not_duplicate += 1
                resolved += 1

            else:
                # Low confidence — leave as pending for manual review
                pair.resolution = f"low_confidence_{confidence:.2f}: {result.get('reasoning', '')[:100]}"
                low_confidence += 1

        db.commit()

        summary = {
            "resolved": resolved,
            "merged": merged,
            "not_duplicate": not_duplicate,
            "low_confidence": low_confidence,
        }
        logger.info("task_complete", task="resolve_dedup_pairs", **summary)
        return summary

    except Exception as exc:
        logger.error("task_failed", task="resolve_dedup_pairs", error=str(exc))
        raise self.retry(exc=exc, countdown=120)

    finally:
        db.close()
