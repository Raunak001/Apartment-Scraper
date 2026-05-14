"""Craigslist Chicago apartment scraper.

Strategy:
  1. Fetch the HTML search page to discover listing URLs.
     (RSS feed is blocked by Craigslist — returns 403.)
  2. For each URL not already in the DB, scrape the detail page for full data.
  3. Extract zip code from the detail page, map to neighborhood.
     Fall back to the search result's location text if no zip is found.
  4. Return a list of normalized listing dicts ready for DB insertion.
"""

import re
import time
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from app.core.config import SCRAPE_DELAY_SECONDS, USER_AGENT, load_preferences
from app.core.logging import get_logger
from app.core.neighborhoods import (
    zip_to_neighborhood,
    log_unmapped_zip_summary,
    reset_unmapped_zips,
)
from app.scrapers.base import RawListing

logger = get_logger(__name__)


def _build_search_url() -> str:
    """Build a filtered Craigslist search URL from preferences.yaml.

    Uses geo-fenced search centered on Chicago's core neighborhoods
    with price/bedroom filters. The search params are intentionally
    wider than alert thresholds so we collect enough data for the
    price distribution model.
    """
    prefs = load_preferences()

    max_price = prefs.get("pricing", {}).get("max_price", 2200)
    min_bedrooms = 0  # always include studios
    max_bedrooms = prefs.get("unit", {}).get("max_bedrooms", 2)

    # Pad max_price by ~20% for the search — we want listings above budget
    # to feed the price model, but not absurdly expensive ones.
    search_max_price = int(max_price * 1.2)

    params = {
        "lat": "41.895",
        "lon": "-87.6441",
        "search_distance": "1.6",
        "min_bedrooms": str(min_bedrooms),
        "max_bedrooms": str(max_bedrooms),
        "max_price": str(search_max_price),
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://chicago.craigslist.org/search/chicago-il/apa?{query}"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Craigslist location text → preferences.yaml neighborhood name.
# Used as a fallback when zip code extraction fails.
LOCATION_TO_NEIGHBORHOOD: dict[str, str] = {
    "lincoln park": "lincoln_park",
    "wicker park": "wicker_park",
    "logan square": "logan_square",
    "old town": "old_town",
    "west loop": "west_loop",
    "west town": "west_town",
    "fulton market": "fulton_market",
    "river north": "river_north",
}


def fetch_search_listings() -> list[dict]:
    """Fetch the Craigslist HTML search page and extract listing summaries.

    Each entry contains: external_id, url, title, price, location.
    Full details come from scraping each detail page.
    """
    search_url = _build_search_url()
    logger.info("fetching_search_page", url=search_url)

    with httpx.Client(headers=HEADERS, timeout=20.0, follow_redirects=True) as client:
        response = client.get(search_url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    results = soup.select("li.cl-static-search-result")

    entries = []
    for result in results:
        link_el = result.select_one("a")
        if not link_el or not link_el.get("href"):
            continue

        url = link_el["href"]
        external_id = _extract_external_id(url)
        if not external_id:
            continue

        title = result.select_one(".title")
        price_el = result.select_one(".price")
        location_el = result.select_one(".location")

        entries.append({
            "external_id": external_id,
            "url": url,
            "title": title.get_text(strip=True) if title else None,
            "price": _parse_price(price_el.get_text(strip=True)) if price_el else None,
            "location": location_el.get_text(strip=True) if location_el else None,
        })

    logger.info("search_page_fetched", count=len(entries))
    return entries


def scrape_listing_detail(url: str) -> dict:
    """Scrape a single Craigslist listing page and extract all available fields.

    Returns a dict of fields suitable for constructing a RawListing.
    """
    logger.debug("scraping_detail", url=url)

    with httpx.Client(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    data: dict = {}

    # Title
    title_el = soup.select_one("#titletextonly")
    if title_el:
        data["title"] = title_el.get_text(strip=True)

    # Price
    price_el = soup.select_one(".price")
    if price_el:
        data["price"] = _parse_price(price_el.get_text(strip=True))

    # Housing attributes: bedrooms, bathrooms, sqft
    attrs_group = soup.select_one(".mapAndAttrs .attrgroup")
    if attrs_group:
        attrs_text = attrs_group.get_text(" ", strip=True)
        data.update(_parse_housing_attrs(attrs_text))

    # Description
    body_el = soup.select_one("#postingbody")
    if body_el:
        # Remove the "QR Code" disclaimer that Craigslist inserts
        for unwanted in body_el.select(".print-information"):
            unwanted.decompose()
        desc = body_el.get_text(strip=True)
        # Remove the standard "QR Code Link to This Post" prefix
        desc = re.sub(r"^QR Code Link to This Post\s*", "", desc)
        data["description"] = desc.strip() if desc.strip() else None

    # Address from map section
    address_el = soup.select_one(".mapaddress")
    if address_el:
        data["address"] = address_el.get_text(strip=True)

    # Zip code from address or map data attributes
    zip_code = _extract_zip_from_soup(soup)
    if zip_code:
        data["zip_code"] = zip_code
        data["neighborhood"] = zip_to_neighborhood(zip_code)

    # Posted date
    time_el = soup.select_one(".postinginfo.reveal time")
    if time_el and time_el.get("datetime"):
        try:
            data["listed_at"] = datetime.fromisoformat(time_el["datetime"])
        except (ValueError, TypeError):
            pass

    return data


def _resolve_neighborhood(detail: dict, search_location: str | None) -> str | None:
    """Determine neighborhood using zip code first, falling back to search location text."""
    # Prefer zip-based mapping (most reliable)
    if detail.get("neighborhood"):
        return detail["neighborhood"]

    # Fall back to the location text from the search results page
    if search_location:
        location_lower = search_location.lower().strip()
        for text, neighborhood in LOCATION_TO_NEIGHBORHOOD.items():
            if text in location_lower:
                return neighborhood

    return None


def scrape_craigslist_listings(known_external_ids: set[str] | None = None) -> list[RawListing]:
    """Full scrape pipeline: search page discovery -> detail scrape for new listings.

    Args:
        known_external_ids: Set of external_ids already in the DB.
                           Listings with these IDs are skipped.

    Returns:
        List of RawListing objects ready for DB insertion.
    """
    if known_external_ids is None:
        known_external_ids = set()

    # Reset unmapped zip tracking for this run
    reset_unmapped_zips()

    search_entries = fetch_search_listings()

    new_entries = [e for e in search_entries if e["external_id"] not in known_external_ids]
    logger.info(
        "new_listings_found",
        total_search=len(search_entries),
        already_known=len(search_entries) - len(new_entries),
        new=len(new_entries),
    )

    listings: list[RawListing] = []
    for entry in new_entries:
        try:
            detail = scrape_listing_detail(entry["url"])

            neighborhood = _resolve_neighborhood(detail, entry.get("location"))

            listing = RawListing(
                external_id=entry["external_id"],
                source="craigslist",
                url=entry["url"],
                title=detail.get("title") or entry.get("title"),
                price=detail.get("price") or entry.get("price"),
                address=detail.get("address"),
                zip_code=detail.get("zip_code"),
                neighborhood=neighborhood,
                bedrooms=detail.get("bedrooms"),
                bathrooms=detail.get("bathrooms"),
                sqft=detail.get("sqft"),
                description=detail.get("description"),
                listed_at=detail.get("listed_at"),
            )
            listings.append(listing)
            logger.info(
                "listing_scraped",
                external_id=listing.external_id,
                price=listing.price,
                neighborhood=listing.neighborhood,
            )

        except httpx.HTTPStatusError as e:
            logger.warning("detail_scrape_http_error", url=entry["url"], status=e.response.status_code)
        except Exception as e:
            logger.error("detail_scrape_error", url=entry["url"], error=str(e))

        # Polite delay between requests
        time.sleep(SCRAPE_DELAY_SECONDS)

    # Log which zip codes we saw but couldn't map to a neighborhood
    log_unmapped_zip_summary()

    logger.info("scrape_complete", total_scraped=len(listings))
    return listings


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _extract_external_id(url: str) -> str | None:
    """Extract the numeric Craigslist posting ID from a URL.

    Example: https://chicago.craigslist.org/chc/apa/d/chicago-nice-place/7841234567.html
    Returns: '7841234567'
    """
    match = re.search(r"/(\d{8,12})\.html", url)
    return match.group(1) if match else None


def _parse_price(text: str) -> int | None:
    """Parse a price string like '$1,200' into an integer."""
    match = re.search(r"\$([0-9,]+)", text)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _parse_housing_attrs(text: str) -> dict:
    """Parse the housing attributes text for bedrooms, bathrooms, sqft.

    Example text: '2BR / 1Ba 800ft²'
    """
    result: dict = {}

    # Bedrooms: '2BR' or '2br', or 'studio' (= 0 bedrooms)
    if re.search(r"[Ss]tudio", text):
        result["bedrooms"] = 0
    br_match = re.search(r"(\d+)\s*[Bb][Rr]", text)
    if br_match:
        result["bedrooms"] = int(br_match.group(1))

    # Bathrooms: '1Ba' or '1.5Ba'
    ba_match = re.search(r"(\d+\.?\d*)\s*[Bb][Aa]", text)
    if ba_match:
        result["bathrooms"] = float(ba_match.group(1))

    # Sqft: '800ft²' or '800ft2'
    sqft_match = re.search(r"(\d+)\s*ft", text)
    if sqft_match:
        result["sqft"] = int(sqft_match.group(1))

    return result


def _extract_zip_from_soup(soup: BeautifulSoup) -> str | None:
    """Try to extract a 5-digit zip code from the listing page.

    Checks the map address text and the broader page for Chicago zip patterns.
    """
    # Check the map address first
    address_el = soup.select_one(".mapaddress")
    if address_el:
        addr_text = address_el.get_text()
        zip_match = re.search(r"\b(606\d{2})\b", addr_text)
        if zip_match:
            return zip_match.group(1)

    # Fall back: search the posting body for a Chicago zip
    body = soup.select_one("#postingbody")
    if body:
        body_text = body.get_text()
        zip_match = re.search(r"\b(606\d{2})\b", body_text)
        if zip_match:
            return zip_match.group(1)

    return None
