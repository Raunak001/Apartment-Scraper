"""Celery tasks for the apartment scraper pipeline."""

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import get_logger, setup_logging
from app.models.listing import Listing
from app.scrapers.base import RawListing
from app.scrapers.craigslist import scrape_craigslist_listings
from app.scrapers.domu import scrape_domu_listings
from app.workers.celery_app import celery_app
from app.workers.insert_helpers import batch_insert_listings

# Initialize logging for worker processes
setup_logging()
logger = get_logger(__name__)


def _load_known_ids(db, source: str) -> set[str]:
    """Load all known external IDs for a given source."""
    return set(
        row[0]
        for row in db.execute(
            select(Listing.external_id).where(Listing.source == source)
        ).all()
    )


@celery_app.task(name="app.workers.tasks.scrape_craigslist", bind=True, max_retries=3)
def scrape_craigslist(self) -> dict:
    """Scrape Craigslist Chicago, deduplicate, and insert new listings into the DB."""
    logger.info("task_started", task="scrape_craigslist")

    db = SessionLocal()
    try:
        known_ids = _load_known_ids(db, "craigslist")
        logger.info("known_listings_loaded", source="craigslist", count=len(known_ids))

        raw_listings: list[RawListing] = scrape_craigslist_listings(known_external_ids=known_ids)
        result = batch_insert_listings(raw_listings, db)

        summary = {
            "source": "craigslist",
            "already_known": len(known_ids),
            "scraped": len(raw_listings),
            **result,
        }
        logger.info("task_complete", task="scrape_craigslist", **summary)
        return summary

    except Exception as exc:
        logger.error("task_failed", task="scrape_craigslist", error=str(exc))
        raise self.retry(exc=exc, countdown=60)

    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.scrape_domu", bind=True, max_retries=3)
def scrape_domu(self) -> dict:
    """Scrape Domu Chicago, deduplicate, and insert new listings into the DB."""
    logger.info("task_started", task="scrape_domu")

    db = SessionLocal()
    try:
        known_ids = _load_known_ids(db, "domu")
        logger.info("known_listings_loaded", source="domu", count=len(known_ids))

        raw_listings: list[RawListing] = scrape_domu_listings(known_external_ids=known_ids)
        result = batch_insert_listings(raw_listings, db)

        summary = {
            "source": "domu",
            "already_known": len(known_ids),
            "scraped": len(raw_listings),
            **result,
        }
        logger.info("task_complete", task="scrape_domu", **summary)
        return summary

    except Exception as exc:
        logger.error("task_failed", task="scrape_domu", error=str(exc))
        raise self.retry(exc=exc, countdown=60)

    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.poll_zillow_email", bind=True, max_retries=2)
def poll_zillow_email(self) -> dict:
    """Poll Gmail for Zillow alert emails, parse listings, and insert."""
    from app.email.gmail_client import GmailClient, is_gmail_configured
    from app.email.zillow_parser import ZILLOW_SENDER, parse_zillow_email

    if not is_gmail_configured():
        logger.warning("gmail_not_configured", task="poll_zillow_email")
        return {"skipped": "gmail_not_configured"}

    logger.info("task_started", task="poll_zillow_email")

    client = GmailClient()
    db = SessionLocal()
    try:
        emails = client.fetch_unprocessed(ZILLOW_SENDER)
        if not emails:
            return {"source": "zillow", "emails": 0, "inserted": 0}

        total_inserted = 0
        for email_msg in emails:
            raw_listings = parse_zillow_email(email_msg.html_body)
            if raw_listings:
                result = batch_insert_listings(raw_listings, db)
                total_inserted += result.get("inserted", 0)
            client.mark_processed(email_msg.message_id)

        summary = {"source": "zillow", "emails": len(emails), "inserted": total_inserted}
        logger.info("task_complete", task="poll_zillow_email", **summary)
        return summary

    except Exception as exc:
        logger.error("task_failed", task="poll_zillow_email", error=str(exc))
        raise self.retry(exc=exc, countdown=120)

    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.poll_apartments_email", bind=True, max_retries=2)
def poll_apartments_email(self) -> dict:
    """Poll Gmail for Apartments.com alert emails, parse listings, and insert."""
    from app.email.gmail_client import GmailClient, is_gmail_configured
    from app.email.apartments_parser import APARTMENTS_SENDER, parse_apartments_email

    if not is_gmail_configured():
        logger.warning("gmail_not_configured", task="poll_apartments_email")
        return {"skipped": "gmail_not_configured"}

    logger.info("task_started", task="poll_apartments_email")

    client = GmailClient()
    db = SessionLocal()
    try:
        emails = client.fetch_unprocessed(APARTMENTS_SENDER)
        if not emails:
            return {"source": "apartments_com", "emails": 0, "inserted": 0}

        total_inserted = 0
        for email_msg in emails:
            raw_listings = parse_apartments_email(email_msg.html_body)
            if raw_listings:
                result = batch_insert_listings(raw_listings, db)
                total_inserted += result.get("inserted", 0)
            client.mark_processed(email_msg.message_id)

        summary = {"source": "apartments_com", "emails": len(emails), "inserted": total_inserted}
        logger.info("task_complete", task="poll_apartments_email", **summary)
        return summary

    except Exception as exc:
        logger.error("task_failed", task="poll_apartments_email", error=str(exc))
        raise self.retry(exc=exc, countdown=120)

    finally:
        db.close()
