"""Price distribution and deal scoring service using median + MAD."""

import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.listing import Listing
from app.models.price_distribution import PriceDistribution

logger = get_logger(__name__)

ROLLING_WINDOW_DAYS = 60
MIN_SAMPLE_COUNT = 20
MAD_FLOOR = 1.0
MAD_NORMALIZATION = 0.6745


class PricingService:

    def rebuild_distributions(self, db: Session) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=ROLLING_WINDOW_DAYS)

        rows = db.execute(
            select(Listing.neighborhood, Listing.bedrooms, Listing.price).where(
                Listing.status == "active",
                Listing.price.isnot(None),
                Listing.neighborhood.isnot(None),
                Listing.bedrooms.isnot(None),
                Listing.scraped_at >= cutoff,
            )
        ).all()

        groups: dict[tuple[str, int], list[int]] = defaultdict(list)
        for neighborhood, bedrooms, price in rows:
            groups[(neighborhood, bedrooms)].append(int(price))

        segments_updated = 0
        for (neighborhood, bedroom_count), prices in groups.items():
            median = statistics.median(prices)
            mad = statistics.median([abs(p - median) for p in prices])

            stmt = insert(PriceDistribution).values(
                neighborhood=neighborhood,
                bedroom_count=bedroom_count,
                median_price=round(median, 2),
                mad_price=round(mad, 2),
                sample_count=len(prices),
                last_updated=datetime.now(timezone.utc),
            ).on_conflict_do_update(
                constraint="uq_neighborhood_bedrooms",
                set_={
                    "median_price": round(median, 2),
                    "mad_price": round(mad, 2),
                    "sample_count": len(prices),
                    "last_updated": datetime.now(timezone.utc),
                },
            )
            db.execute(stmt)
            segments_updated += 1

        db.commit()

        logger.info(
            "distributions_rebuilt",
            segments=segments_updated,
            total_listings=len(rows),
            window_days=ROLLING_WINDOW_DAYS,
        )
        return {"segments_updated": segments_updated, "total_sampled": len(rows)}

    def score_listing(self, db: Session, listing: Listing) -> float | None:
        if listing.price is None or listing.neighborhood is None or listing.bedrooms is None:
            return None

        dist = db.execute(
            select(PriceDistribution).where(
                PriceDistribution.neighborhood == listing.neighborhood,
                PriceDistribution.bedroom_count == listing.bedrooms,
            )
        ).scalar_one_or_none()

        if dist is None or dist.sample_count < MIN_SAMPLE_COUNT:
            return None

        if dist.mad_price is None or float(dist.mad_price) < MAD_FLOOR:
            return None

        z = MAD_NORMALIZATION * (listing.price - float(dist.median_price)) / float(dist.mad_price)
        deal_score = 1.0 / (1.0 + math.exp(z))
        return round(deal_score, 4)
