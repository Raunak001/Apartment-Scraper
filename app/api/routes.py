"""FastAPI routes — health checks, listing status, and manual scrape triggers."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enrichment import EnrichmentResult
from app.models.listing import Listing
from app.models.price_distribution import PriceDistribution
from app.workers.tasks import (
    poll_apartments_email,
    poll_zillow_email,
    scrape_craigslist,
    scrape_domu,
)

MIN_SEGMENT_SAMPLES = 5  # Keep in sync with pricing.py MIN_SAMPLE_COUNT

router = APIRouter()


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """Basic health check. Verifies the DB connection is alive."""
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/status")
def scraper_status(db: Session = Depends(get_db)):
    """Dashboard-style status: listing counts by source, most recent scrape time."""
    # Count listings per source
    source_counts = dict(
        db.execute(
            select(Listing.source, func.count(Listing.id)).group_by(Listing.source)
        ).all()
    )

    # Most recent scrape timestamp
    latest_scrape = db.execute(
        select(func.max(Listing.scraped_at))
    ).scalar()

    # Count by status
    status_counts = dict(
        db.execute(
            select(Listing.status, func.count(Listing.id)).group_by(Listing.status)
        ).all()
    )

    # Count by neighborhood (non-null only)
    neighborhood_counts = dict(
        db.execute(
            select(Listing.neighborhood, func.count(Listing.id))
            .where(Listing.neighborhood.isnot(None))
            .group_by(Listing.neighborhood)
        ).all()
    )

    # Deal scoring stats
    scored_count = db.execute(
        select(func.count(Listing.id)).where(Listing.deal_score.isnot(None))
    ).scalar() or 0

    awaiting_score = db.execute(
        select(func.count(Listing.id))
        .select_from(Listing)
        .join(EnrichmentResult, Listing.id == EnrichmentResult.listing_id)
        .where(
            EnrichmentResult.skipped.is_(False),
            EnrichmentResult.failed.is_(False),
            Listing.deal_score.is_(None),
        )
    ).scalar() or 0

    total_segments = db.execute(
        select(func.count(PriceDistribution.id))
    ).scalar() or 0

    gated_segments = db.execute(
        select(func.count(PriceDistribution.id)).where(PriceDistribution.sample_count >= MIN_SEGMENT_SAMPLES)
    ).scalar() or 0

    return {
        "total_listings": sum(source_counts.values()),
        "by_source": source_counts,
        "by_status": status_counts,
        "by_neighborhood": neighborhood_counts,
        "latest_scrape": latest_scrape.isoformat() if latest_scrape else None,
        "scoring": {
            "scored": scored_count,
            "awaiting_score": awaiting_score,
            "total_segments": total_segments,
            "segments_meeting_gate": gated_segments,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/listings")
def list_listings(
    db: Session = Depends(get_db),
    neighborhood: str | None = Query(None, description="Filter by neighborhood"),
    max_price: int | None = Query(None, description="Maximum price"),
    min_bedrooms: int | None = Query(None, description="Minimum bedrooms"),
    sort_by: str | None = Query(None, description="Sort by: composite_score, deal_score, price"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List stored listings with optional filters."""
    query = select(Listing).where(Listing.status == "active")

    if neighborhood:
        query = query.where(Listing.neighborhood == neighborhood)
    if max_price is not None:
        query = query.where(Listing.price <= max_price)
    if min_bedrooms is not None:
        query = query.where(Listing.bedrooms >= min_bedrooms)

    sort_columns = {
        "composite_score": Listing.composite_score.desc().nullslast(),
        "deal_score": Listing.deal_score.desc().nullslast(),
        "price": Listing.price.asc().nullslast(),
    }
    order = sort_columns.get(sort_by, Listing.scraped_at.desc())
    query = query.order_by(order).offset(offset).limit(limit)
    listings = db.execute(query).scalars().all()

    return {
        "count": len(listings),
        "listings": [
            {
                "id": str(l.id),
                "external_id": l.external_id,
                "source": l.source,
                "url": l.url,
                "title": l.title,
                "price": l.price,
                "bedrooms": l.bedrooms,
                "bathrooms": float(l.bathrooms) if l.bathrooms else None,
                "sqft": l.sqft,
                "neighborhood": l.neighborhood,
                "address": l.address,
                "deal_score": float(l.deal_score) if l.deal_score is not None else None,
                "composite_score": float(l.composite_score) if l.composite_score is not None else None,
                "listed_at": l.listed_at.isoformat() if l.listed_at else None,
                "scraped_at": l.scraped_at.isoformat() if l.scraped_at else None,
            }
            for l in listings
        ],
    }


@router.get("/price-distributions")
def price_distributions(
    db: Session = Depends(get_db),
    neighborhood: str | None = Query(None, description="Filter by neighborhood"),
    min_samples: int = Query(0, ge=0, description="Minimum sample count"),
):
    """Return price distribution stats per neighborhood × bedroom segment."""
    query = select(PriceDistribution)

    if neighborhood:
        query = query.where(PriceDistribution.neighborhood == neighborhood)
    if min_samples > 0:
        query = query.where(PriceDistribution.sample_count >= min_samples)

    query = query.order_by(PriceDistribution.neighborhood, PriceDistribution.bedroom_count)
    distributions = db.execute(query).scalars().all()

    return {
        "count": len(distributions),
        "distributions": [
            {
                "neighborhood": d.neighborhood,
                "bedroom_count": d.bedroom_count,
                "median_price": float(d.median_price) if d.median_price is not None else None,
                "mad_price": float(d.mad_price) if d.mad_price is not None else None,
                "sample_count": d.sample_count,
                "gate_met": d.sample_count >= MIN_SEGMENT_SAMPLES,
                "last_updated": d.last_updated.isoformat() if d.last_updated else None,
            }
            for d in distributions
        ],
    }


# ---------------------------------------------------------------------------
# Manual scrape triggers
# ---------------------------------------------------------------------------

@router.post("/scrape/craigslist")
def trigger_scrape_craigslist():
    """Manually queue a Craigslist scrape task."""
    result = scrape_craigslist.delay()
    return {"status": "queued", "task_id": result.id, "source": "craigslist"}


@router.post("/scrape/domu")
def trigger_scrape_domu():
    """Manually queue a Domu scrape task."""
    result = scrape_domu.delay()
    return {"status": "queued", "task_id": result.id, "source": "domu"}


@router.post("/scrape/email")
def trigger_scrape_email():
    """Manually queue email polling for both Zillow and Apartments.com."""
    zillow = poll_zillow_email.delay()
    apartments = poll_apartments_email.delay()
    return {
        "status": "queued",
        "tasks": {
            "zillow": zillow.id,
            "apartments_com": apartments.id,
        },
    }


@router.post("/scrape/all")
def trigger_scrape_all():
    """Manually queue all four scrape sources at once."""
    cl = scrape_craigslist.delay()
    domu = scrape_domu.delay()
    zillow = poll_zillow_email.delay()
    apartments = poll_apartments_email.delay()
    return {
        "status": "queued",
        "tasks": {
            "craigslist": cl.id,
            "domu": domu.id,
            "zillow": zillow.id,
            "apartments_com": apartments.id,
        },
    }
