"""Domu Chicago apartment scraper via JSON API.

Strategy:
  1. Hit the /find/map/markers endpoint with map bounds covering Chicago
     and filters from preferences.yaml (price, amenities).
  2. Parse the structured JSON response — each marker contains price,
     beds, baths, sqft, address, neighborhood, and a stable listing ID.
  3. Filter to target neighborhoods and skip known external IDs.
  4. Optionally scrape detail pages for description text.
  5. Return normalized RawListing objects.

This replaces the old neighborhood-page-crawling approach. One API call
gets all listings vs. dozens of page scrapes + detail page visits.
"""

import re
import time

import httpx

from app.core.config import SCRAPE_DELAY_SECONDS, USER_AGENT, load_preferences
from app.core.logging import get_logger
from app.scrapers.base import RawListing

logger = get_logger(__name__)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.5",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.domu.com/chicago-il/apartments",
}

BASE_URL = "https://www.domu.com"
MARKERS_ENDPOINT = f"{BASE_URL}/find/map/markers"

# Map bounds covering the Chicago neighborhoods we care about.
# Generous enough to include all target areas with margin.
CHICAGO_BOUNDS = {
    "sw": "41.84,-87.72",   # Southwest corner (south of Pilsen)
    "ne": "41.97,-87.58",   # Northeast corner (north of Lincoln Park)
}

# Domu neighborhood names -> preferences.yaml neighborhood keys.
# Domu returns neighborhood as "West Loop", "Lincoln Park", etc.
DOMU_HOOD_TO_PREF: dict[str, str] = {
    "lincoln park": "lincoln_park",
    "wicker park": "wicker_park",
    "logan square": "logan_square",
    "old town": "old_town",
    "west loop": "west_loop",
    "west town": "west_town",
    "fulton market": "fulton_market",
    "fulton market district": "fulton_market",
    "river north": "river_north",
}


def _parse_price(price_str: str | None) -> int | None:
    """Parse a price string like '$3,000' or '3000' into an integer."""
    if not price_str:
        return None
    match = re.search(r"[\$]?([0-9,]+)", str(price_str))
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _parse_bedrooms(bedroom_str: str | None) -> int | None:
    """Parse bedroom string like '1 Bedroom', 'Studio', '2 Bedrooms'."""
    if not bedroom_str:
        return None
    if "studio" in str(bedroom_str).lower():
        return 0
    match = re.search(r"(\d+)", str(bedroom_str))
    return int(match.group(1)) if match else None


def _parse_bathrooms(bathroom_str: str | None) -> float | None:
    """Parse bathroom string like '1', '1.5', '2'."""
    if not bathroom_str:
        return None
    try:
        return float(str(bathroom_str))
    except (ValueError, TypeError):
        return None


def _parse_sqft(area_str: str | None) -> int | None:
    """Parse square footage string like '646' or '1,200'."""
    if not area_str:
        return None
    match = re.search(r"([0-9,]+)", str(area_str))
    if match:
        val = int(match.group(1).replace(",", ""))
        return val if val > 0 else None
    return None


def _extract_zip(address: str | None) -> str | None:
    """Extract a Chicago zip code from an address string."""
    if not address:
        return None
    match = re.search(r"\b(606\d{2})\b", address)
    return match.group(1) if match else None


def _extract_unit(title: str | None) -> str | None:
    """Extract unit number from a title like '1220 W Jackson Blvd #ID1319'."""
    if not title:
        return None
    match = re.search(r"#(\w+)", title)
    return match.group(1) if match else None


def _resolve_neighborhood(hood_name: str | None) -> str | None:
    """Map Domu's neighborhood name to preferences.yaml key."""
    if not hood_name:
        return None
    return DOMU_HOOD_TO_PREF.get(hood_name.lower().strip())


def _fetch_markers(prefs: dict) -> list[dict]:
    """Fetch all listing markers from the Domu JSON API.

    Applies price and amenity filters from preferences.yaml.
    Returns the raw markers list from the API response.
    """
    max_price = prefs.get("pricing", {}).get("max_price", 2200)
    # Pad by 20% to match Craigslist strategy — collect above-budget
    # listings for the price distribution model
    search_max_price = int(max_price * 1.2)

    params: dict[str, str] = {
        "sw": CHICAGO_BOUNDS["sw"],
        "ne": CHICAGO_BOUNDS["ne"],
        "domu_rentalprice_max": str(search_max_price),
        "sort": "acttime",
        "limit": "1000",
    }

    # Apply amenity filters
    required_amenities = prefs.get("amenities", {}).get("required", [])
    if "in_unit_laundry" in required_amenities:
        params["domu_washerdrier"] = "Washer/Dryer: In-Unit"

    logger.info("domu_fetching_markers", params=params)

    try:
        with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
            response = client.get(MARKERS_ENDPOINT, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        logger.error("domu_api_error", status=e.response.status_code)
        return []
    except Exception as e:
        logger.error("domu_api_error", error=str(e))
        return []

    markers = data.get("markers", [])
    logger.info("domu_markers_fetched", total=len(markers))
    return markers


def _fetch_description(detail_url: str) -> str | None:
    """Scrape the listing description from a detail page.

    Only called for listings that pass all filters, to minimize requests.
    """
    try:
        html_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        with httpx.Client(headers=html_headers, timeout=15.0, follow_redirects=True) as client:
            response = client.get(detail_url)
            response.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "lxml")

        # Try "Unique Features" section first, then "About"/"Description"
        for heading in soup.find_all(["h2", "h3"]):
            heading_text = heading.get_text(strip=True).lower()
            if any(kw in heading_text for kw in ["unique features", "about", "description"]):
                parts = []
                for sibling in heading.find_next_siblings():
                    if sibling.name in ("h2", "h3"):
                        break
                    text = sibling.get_text(strip=True)
                    if text:
                        parts.append(text)
                if parts:
                    return " ".join(parts)

        return None

    except Exception as e:
        logger.warning("domu_description_fetch_error", url=detail_url, error=str(e))
        return None


def scrape_domu_listings(known_external_ids: set[str] | None = None) -> list[RawListing]:
    """Full Domu scrape pipeline: JSON API -> filter -> optional detail scrape -> RawListings."""
    if known_external_ids is None:
        known_external_ids = set()

    prefs = load_preferences()
    target_neighborhoods = set(prefs.get("search", {}).get("neighborhoods", []))

    markers = _fetch_markers(prefs)
    if not markers:
        return []

    # Extract detail URLs from the listings HTML for description scraping.
    # The markers API also returns an HTML blob with listing card links.
    # We'll build a nid -> URL map if we need descriptions later.

    listings: list[RawListing] = []

    for marker in markers:
        try:
            rent = marker.get("rent", {})
            if not rent:
                continue

            nid = str(rent.get("nid", ""))
            if not nid:
                continue

            # Skip already-known listings
            if nid in known_external_ids:
                continue

            # Parse fields from the structured JSON
            title = rent.get("title", "")
            address = rent.get("address", "")
            price = _parse_price(rent.get("price") or rent.get("price_formatted"))
            bedrooms = _parse_bedrooms(rent.get("bedroom_formatted") or rent.get("bedroom"))
            bathrooms = _parse_bathrooms(rent.get("bathroom_formatted"))
            sqft = _parse_sqft(rent.get("area"))
            hood_name = rent.get("hood", "")
            zip_code = _extract_zip(address)
            unit_number = _extract_unit(title)

            # Map to preferences.yaml neighborhood
            neighborhood = _resolve_neighborhood(hood_name)

            # Filter: only keep listings in our target neighborhoods
            if neighborhood and neighborhood not in target_neighborhoods:
                continue
            # If Domu didn't return a neighborhood, keep it (we'll try zip mapping later)

            listing = RawListing(
                external_id=nid,
                source="domu",
                url=f"{BASE_URL}/node/{nid}",  # Canonical URL; detail URL built later if needed
                title=title or None,
                price=price,
                address=address or None,
                unit_number=unit_number,
                zip_code=zip_code,
                neighborhood=neighborhood,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                sqft=sqft,
                description=None,  # Populated below if we scrape detail pages
            )
            listings.append(listing)

        except Exception as e:
            logger.error("domu_marker_parse_error", marker_nid=marker.get("rent", {}).get("nid"), error=str(e))

    logger.info(
        "domu_listings_parsed",
        total_markers=len(markers),
        after_filter=len(listings),
        skipped_known=len(known_external_ids),
    )

    # Optionally fetch descriptions for new listings from detail pages.
    # This is the only part that requires multiple HTTP requests.
    # Cap at 30 detail fetches per run to stay polite.
    MAX_DETAIL_FETCHES = 30
    detail_count = 0

    for listing in listings:
        if detail_count >= MAX_DETAIL_FETCHES:
            break

        # Build the detail URL from the listing card HTML if available,
        # otherwise use the /node/NID redirect which Domu supports
        detail_url = f"{BASE_URL}/node/{listing.external_id}"

        description = _fetch_description(detail_url)
        if description:
            listing.description = description
            detail_count += 1

        time.sleep(SCRAPE_DELAY_SECONDS)

    logger.info("domu_scrape_complete", total_scraped=len(listings), descriptions_fetched=detail_count)
    return listings
