"""Stage-1 cross-source deduplication service.

Two mechanisms:
  1. Hash dedup (at insert time) — exact match on normalized address+unit+bedrooms.
     Handled by insert_helpers.py via compute_dedup_hash().

  2. Fuzzy dedup (periodic sweep) — rapidfuzz on address+unit across sources.
     Catches near-matches that differ by abbreviation, formatting, etc.
"""

from datetime import datetime, timedelta, timezone

from rapidfuzz import fuzz
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.dedup import DedupPair
from app.models.listing import Listing
from app.workers.insert_helpers import normalize_address

logger = get_logger(__name__)

FUZZY_THRESHOLD = 85
PRICE_DIFF_AUTO_MERGE_PCT = 0.10
SWEEP_WINDOW_HOURS = 48


def _address_key(listing: Listing) -> str:
    """Build a normalized address key for fuzzy comparison."""
    addr = normalize_address(listing.address)
    unit = (listing.unit_number or "").lower().strip()
    if unit:
        addr = f"{addr} {unit}"
    return addr


def _price_diff_pct(price_a: int | None, price_b: int | None) -> float:
    """Calculate percentage price difference between two listings."""
    if not price_a or not price_b:
        return 0.0
    avg = (price_a + price_b) / 2
    if avg == 0:
        return 0.0
    return abs(price_a - price_b) / avg


def _pair_exists(db: Session, id_a, id_b) -> bool:
    """Check if a dedup pair already exists (in either direction)."""
    return db.execute(
        select(DedupPair.id).where(
            ((DedupPair.listing_a_id == id_a) & (DedupPair.listing_b_id == id_b))
            | ((DedupPair.listing_a_id == id_b) & (DedupPair.listing_b_id == id_a))
        )
    ).first() is not None


def cross_source_dedup_sweep(db: Session) -> dict:
    """Run fuzzy dedup across all active listings from the last 48 hours.

    Groups by neighborhood to limit pairwise comparisons.
    Returns a summary dict.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SWEEP_WINDOW_HOURS)

    listings = db.execute(
        select(Listing).where(
            Listing.status == "active",
            Listing.scraped_at >= cutoff,
            Listing.address.isnot(None),
        )
    ).scalars().all()

    # Group by neighborhood for manageable pairwise comparisons
    by_neighborhood: dict[str | None, list[Listing]] = {}
    for listing in listings:
        key = listing.neighborhood or listing.zip_code
        by_neighborhood.setdefault(key, []).append(listing)

    auto_merged = 0
    flagged = 0
    comparisons = 0

    for group_key, group in by_neighborhood.items():
        if len(group) < 2:
            continue

        # Only compare listings from different sources
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.source == b.source:
                    continue

                comparisons += 1
                key_a = _address_key(a)
                key_b = _address_key(b)

                if not key_a or not key_b:
                    continue

                score = fuzz.token_sort_ratio(key_a, key_b)
                if score < FUZZY_THRESHOLD:
                    continue

                if _pair_exists(db, a.id, b.id):
                    continue

                diff = _price_diff_pct(a.price, b.price)

                if diff <= PRICE_DIFF_AUTO_MERGE_PCT:
                    # Auto-merge: keep the cheaper listing
                    if (a.price or float("inf")) <= (b.price or float("inf")):
                        b.status = "dedup_duplicate"
                        logger.info(
                            "dedup_auto_merged",
                            kept=str(a.id),
                            merged=str(b.id),
                            score=score,
                        )
                    else:
                        a.status = "dedup_duplicate"
                        logger.info(
                            "dedup_auto_merged",
                            kept=str(b.id),
                            merged=str(a.id),
                            score=score,
                        )
                    auto_merged += 1
                else:
                    # Flag for review (price difference too large for auto-merge)
                    pair = DedupPair(
                        listing_a_id=a.id,
                        listing_b_id=b.id,
                        similarity_score=score,
                        price_diff_pct=diff,
                        status="pending",
                    )
                    db.add(pair)
                    flagged += 1
                    logger.info(
                        "dedup_flagged",
                        listing_a=str(a.id),
                        listing_b=str(b.id),
                        score=score,
                        price_diff=f"{diff:.1%}",
                    )

    db.commit()

    summary = {
        "comparisons": comparisons,
        "auto_merged": auto_merged,
        "flagged_for_review": flagged,
    }
    logger.info("dedup_sweep_complete", **summary)
    return summary
