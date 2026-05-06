"""Celery tasks for the apartment scraper pipeline."""

from dataclasses import asdict

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.models.listing import Listing
from app.scrapers.craigslist import RawListing, scrape_craigslist_listings
from app.workers.celery_app import celery_app

# Initialize logging for worker processes
setup_logging()
logger = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.scrape_craigslist", bind=True, max_retries=3)
def scrape_craigslist(self) -> dict:
    """Scrape Craigslist Chicago, deduplicate, and insert new listings into the DB.

    Returns a summary dict with counts for monitoring.
    """
    logger.info("task_started", task="scrape_craigslist")

    db = SessionLocal()
    try:
        # Get all known Craigslist external IDs for dedup
        known_ids = set(
            row[0]
            for row in db.execute(
                select(Listing.external_id).where(Listing.source == "craigslist")
            ).all()
        )
        logger.info("known_listings_loaded", count=len(known_ids))

        # Scrape new listings
        raw_listings: list[RawListing] = scrape_craigslist_listings(known_external_ids=known_ids)

        # Insert new listings into the DB
        inserted = 0
        skipped = 0
        for raw in raw_listings:
            try:
                listing = Listing(
                    external_id=raw.external_id,
                    source=raw.source,
                    url=raw.url,
                    title=raw.title,
                    address=raw.address,
                    unit_number=raw.unit_number,
                    neighborhood=raw.neighborhood,
                    city=raw.city,
                    zip_code=raw.zip_code,
                    price=raw.price,
                    bedrooms=raw.bedrooms,
                    bathrooms=raw.bathrooms,
                    sqft=raw.sqft,
                    description=raw.description,
                    listed_at=raw.listed_at,
                )
                db.add(listing)
                db.commit()
                inserted += 1
            except Exception as e:
                db.rollback()
                # UniqueConstraint violation = already exists, skip silently
                if "uq_source_external_id" in str(e):
                    skipped += 1
                    logger.debug("duplicate_skipped", external_id=raw.external_id)
                else:
                    logger.error(
                        "insert_error",
                        external_id=raw.external_id,
                        error=str(e),
                    )

        summary = {
            "rss_total": len(raw_listings) + len(known_ids),
            "already_known": len(known_ids),
            "scraped": len(raw_listings),
            "inserted": inserted,
            "skipped_duplicate": skipped,
        }
        logger.info("task_complete", task="scrape_craigslist", **summary)
        return summary

    except Exception as exc:
        logger.error("task_failed", task="scrape_craigslist", error=str(exc))
        raise self.retry(exc=exc, countdown=60)

    finally:
        db.close()
