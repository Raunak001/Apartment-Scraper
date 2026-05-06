"""FastAPI routes — health checks and listing status endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.listing import Listing

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

    return {
        "total_listings": sum(source_counts.values()),
        "by_source": source_counts,
        "by_status": status_counts,
        "by_neighborhood": neighborhood_counts,
        "latest_scrape": latest_scrape.isoformat() if latest_scrape else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/listings")
def list_listings(
    db: Session = Depends(get_db),
    neighborhood: str | None = Query(None, description="Filter by neighborhood"),
    max_price: int | None = Query(None, description="Maximum price"),
    min_bedrooms: int | None = Query(None, description="Minimum bedrooms"),
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

    query = query.order_by(Listing.scraped_at.desc()).offset(offset).limit(limit)
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
                "listed_at": l.listed_at.isoformat() if l.listed_at else None,
                "scraped_at": l.scraped_at.isoformat() if l.scraped_at else None,
            }
            for l in listings
        ],
    }
