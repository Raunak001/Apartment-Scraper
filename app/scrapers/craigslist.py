"""Craigslist Chicago apartment scraper.

Strategy:
  1. Fetch the RSS feed to discover new listing URLs.
  2. For each URL not already in the DB, scrape the detail page for full data.
  3. Extract zip code from the map/address section, map to neighborhood.
  4. Return a list of normalized listing dicts ready for DB insertion.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.core.config import SCRAPE_DELAY_SECONDS, USER_AGENT
from app.core.logging import get_logger
from app.core.neighborhoods import zip_to_neighborhood

logger = get_logger(__name__)

CRAIGSLIST_RSS_URL = "https://chicago.craigslist.org/search/chc/apa?format=rss"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


@dataclass
class RawListing:
    """Intermediate representation of a scraped listing before DB insertion."""

    external_id: str
    source: str = "craigslist"
    url: str = ""
    title: str | None = None
    address: str | None = None
    unit_number: str | None = None
    neighborhood: str | None = None
    city: str = "chicago"
    zip_code: str | None = None
    price: int | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    sqft: int | None = None
    description: str | None = None
    listed_at: datetime | None = None
    extras: dict = field(default_factory=dict)


def fetch_rss_listings() -> list[dict]:
    """Fetch the Craigslist RSS feed and return a list of {external_id, url, title, price}.

    The RSS feed gives us enough to know which listings exist and their URLs.
    Full details come from scraping each detail page.
    """
    logger.info("fetching_rss_feed", url=CRAIGSLIST_RSS_URL)

    feed = feedparser.parse(CRAIGSLIST_RSS_URL)

    if feed.bozo:
        logger.warning("rss_parse_warning", error=str(feed.bozo_exception))

    entries = []
    for entry in feed.entries:
        url = entry.get("link", "")
        external_id = _extract_external_id(url)
        if not external_id:
            continue

        # RSS title often looks like: "$1,200 / 2br - 800ft² - Nice apartment in Lincoln Park"
        title = entry.get("title", "")
        price = _extract_price_from_title(title)

        entries.append({
            "external_id": external_id,
            "url": url,
            "title": title,
            "price": price,
        })

    logger.info("rss_feed_fetched", count=len(entries))
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


def scrape_craigslist_listings(known_external_ids: set[str] | None = None) -> list[RawListing]:
    """Full scrape pipeline: RSS discovery → detail scrape for new listings.

    Args:
        known_external_ids: Set of external_ids already in the DB.
                           Listings with these IDs are skipped.

    Returns:
        List of RawListing objects ready for DB insertion.
    """
    if known_external_ids is None:
        known_external_ids = set()

    rss_entries = fetch_rss_listings()

    new_entries = [e for e in rss_entries if e["external_id"] not in known_external_ids]
    logger.info(
        "new_listings_found",
        total_rss=len(rss_entries),
        already_known=len(rss_entries) - len(new_entries),
        new=len(new_entries),
    )

    listings: list[RawListing] = []
    for entry in new_entries:
        try:
            detail = scrape_listing_detail(entry["url"])

            listing = RawListing(
                external_id=entry["external_id"],
                url=entry["url"],
                title=detail.get("title") or entry.get("title"),
                price=detail.get("price") or entry.get("price"),
                address=detail.get("address"),
                zip_code=detail.get("zip_code"),
                neighborhood=detail.get("neighborhood"),
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


def _extract_price_from_title(title: str) -> int | None:
    """Extract price from RSS title like '$1,200 / 2br - ...'"""
    match = re.search(r"\$([0-9,]+)", title)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


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

    # Bedrooms: '2BR' or '2br'
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
