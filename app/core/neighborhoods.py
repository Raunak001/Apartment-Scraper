"""Chicago zip code to neighborhood mapping.

Used to assign a neighborhood to a listing based on the zip code
extracted from the listing's location data.

Unmapped zip codes are tracked and logged so we can identify gaps
in coverage and decide whether to add new neighborhoods.
"""

from collections import Counter

from app.core.logging import get_logger

logger = get_logger(__name__)

# Tracks zip codes seen during a scrape run that aren't in our mapping.
# Reset between runs; logged at the end of each scrape cycle.
_unmapped_zip_counts: Counter = Counter()

# Mapping of Chicago zip codes to the neighborhood names used in preferences.yaml.
# A zip code can span multiple neighborhoods — we pick the dominant one.
# Source: USPS + Chicago neighborhood boundaries.
ZIP_TO_NEIGHBORHOOD: dict[str, str] = {
    # Lincoln Park
    "60614": "lincoln_park",
    "60657": "lincoln_park",  # also Lakeview — LP is dominant for southern 60657

    # Wicker Park (within the broader West Town community area)
    "60622": "wicker_park",

    # Logan Square
    "60647": "logan_square",

    # Old Town
    "60610": "old_town",

    # West Loop (includes Fulton Market district — same zip, no clean split)
    "60607": "west_loop",
    "60661": "west_loop",

    # West Town (60612 covers the western portion; 60622 overlaps with Wicker Park
    # and defaults to wicker_park since it's the more specific neighborhood name)
    "60612": "west_town",

    # Fulton Market shares 60607 with West Loop. Distinguishing them would require
    # address-level parsing (e.g., streets between Randolph and Lake, Halsted to Ashland).
    # For now, 60607 → west_loop. Fulton Market detection is a future refinement.

    # River North
    "60654": "river_north",
    "60611": "river_north",  # also Streeterville — River North is dominant for western 60611
}

# Reverse lookup: which zip codes are relevant per neighborhood
NEIGHBORHOOD_ZIPS: dict[str, list[str]] = {}
for _zip, _hood in ZIP_TO_NEIGHBORHOOD.items():
    NEIGHBORHOOD_ZIPS.setdefault(_hood, []).append(_zip)


def zip_to_neighborhood(zip_code: str | None) -> str | None:
    """Return the neighborhood name for a Chicago zip code, or None if unmapped.

    Unmapped zip codes are tracked in _unmapped_zip_counts so we can
    identify coverage gaps after each scrape run.
    """
    if not zip_code:
        return None
    # Handle 9-digit zip codes (e.g., 60614-1234)
    zip5 = zip_code.strip()[:5]
    neighborhood = ZIP_TO_NEIGHBORHOOD.get(zip5)
    if neighborhood is None and zip5.startswith("606"):
        _unmapped_zip_counts[zip5] += 1
        logger.debug("unmapped_zip", zip_code=zip5)
    return neighborhood


def get_unmapped_zips() -> dict[str, int]:
    """Return a copy of unmapped zip counts seen since last reset."""
    return dict(_unmapped_zip_counts)


def reset_unmapped_zips() -> None:
    """Clear the unmapped zip counter. Call at the start of each scrape run."""
    _unmapped_zip_counts.clear()


def log_unmapped_zip_summary() -> None:
    """Log a summary of unmapped zip codes seen during this scrape run."""
    if _unmapped_zip_counts:
        logger.warning(
            "unmapped_zips_summary",
            count=sum(_unmapped_zip_counts.values()),
            zips=dict(_unmapped_zip_counts.most_common(10)),
        )
    else:
        logger.info("unmapped_zips_summary", count=0)
