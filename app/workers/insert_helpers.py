"""Shared insert logic for all scraper and email-ingest tasks."""

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.listing import Listing
from app.scrapers.base import RawListing
from app.workers.enrichment_tasks import enrich_listing

logger = get_logger(__name__)


def normalize_address(address: str | None) -> str:
    """Normalize an address for dedup hashing."""
    if not address:
        return ""
    addr = address.lower().strip()
    addr = re.sub(r"[^\w\s]", "", addr)
    replacements = {
        " st ": " street ",
        " ave ": " avenue ",
        " blvd ": " boulevard ",
        " dr ": " drive ",
        " rd ": " road ",
        " ln ": " lane ",
        " ct ": " court ",
        " pl ": " place ",
        " apt ": " ",
        " unit ": " ",
        " ste ": " ",
        " suite ": " ",
        " # ": " ",
    }
    addr = f" {addr} "
    for old, new in replacements.items():
        addr = addr.replace(old, new)
    addr = re.sub(r"\s+", " ", addr).strip()
    if addr.startswith("the "):
        addr = addr[4:]
    return addr


def compute_dedup_hash(address: str | None, unit_number: str | None, bedrooms: int | None) -> str:
    """Compute a deterministic hash for cross-source dedup."""
    normalized = normalize_address(address)
    unit = (unit_number or "").lower().strip()
    beds = str(bedrooms) if bedrooms is not None else ""
    key = f"{normalized}|{unit}|{beds}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def batch_insert_listings(raw_listings: list[RawListing], db: Session) -> dict:
    """Insert a batch of RawListings into the DB with savepoint-per-listing.

    For each listing:
      1. Compute dedup_hash and check for cross-source hash collision.
      2. Insert with a savepoint so one failure doesn't roll back the batch.
      3. Chain enrich_listing for each successfully inserted listing.

    Returns a summary dict with counts.
    """
    inserted = 0
    skipped_duplicate = 0
    skipped_dedup = 0
    errors = 0
    listing_ids: list[str] = []

    for raw in raw_listings:
        savepoint = db.begin_nested()
        try:
            dedup_hash = compute_dedup_hash(raw.address, raw.unit_number, raw.bedrooms)

            if raw.address:
                existing = db.execute(
                    select(Listing).where(
                        Listing.dedup_hash == dedup_hash,
                        Listing.source != raw.source,
                        Listing.status == "active",
                    )
                ).scalar_one_or_none()

                if existing:
                    if raw.price and existing.price and raw.price < existing.price:
                        existing.status = "dedup_duplicate"
                        logger.info(
                            "dedup_cross_source_kept_new",
                            new_source=raw.source,
                            existing_source=existing.source,
                            existing_id=str(existing.id),
                            price_new=raw.price,
                            price_existing=existing.price,
                        )
                    else:
                        skipped_dedup += 1
                        savepoint.rollback()
                        logger.info(
                            "dedup_cross_source_skipped",
                            new_source=raw.source,
                            existing_source=existing.source,
                            existing_id=str(existing.id),
                        )
                        continue

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
                dedup_hash=dedup_hash,
            )
            db.add(listing)
            savepoint.commit()
            listing_ids.append(str(listing.id))
            inserted += 1

        except Exception as e:
            savepoint.rollback()
            if "uq_source_external_id" in str(e):
                skipped_duplicate += 1
                logger.debug("duplicate_skipped", external_id=raw.external_id)
            else:
                errors += 1
                logger.error("insert_error", external_id=raw.external_id, error=str(e))

    db.commit()

    for lid in listing_ids:
        enrich_listing.delay(lid)

    summary = {
        "inserted": inserted,
        "skipped_duplicate": skipped_duplicate,
        "skipped_dedup": skipped_dedup,
        "errors": errors,
    }
    logger.info("batch_insert_complete", **summary)
    return summary
