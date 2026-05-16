"""Parse Apartments.com email alerts into RawListing objects.

Apartments.com sends HTML emails when saved searches match new listings.
Key difference from Zillow: prices may be ranges ("$1,500 - $1,800").
We take the lower bound and store the full range in extras.
"""

import re

from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.core.neighborhoods import zip_to_neighborhood
from app.scrapers.base import RawListing

logger = get_logger(__name__)

APARTMENTS_SENDER = "hello@email.apartments.com"


def _extract_listing_id(url: str) -> str | None:
    """Extract a listing ID from an Apartments.com URL."""
    # Pattern: /apartments/property-name/unit-id/
    match = re.search(r"/(\d{6,12})/?(?:\?|$)", url)
    if match:
        return match.group(1)
    # Fall back: use the last path segment as ID
    parts = url.rstrip("/").split("/")
    slug = parts[-1] if parts else None
    if slug and not slug.startswith("?"):
        return slug
    return None


def _parse_price_range(text: str) -> tuple[int | None, str | None]:
    """Parse price text that may be a range.

    Returns (price, price_range_str).
    For "$1,500 - $1,800": returns (1500, "$1,500 - $1,800")
    For "$1,500/mo": returns (1500, None)
    """
    # Range pattern: "$1,500 - $1,800" or "$1,500-$1,800"
    range_match = re.search(r"\$([0-9,]+)\s*[-–—]\s*\$([0-9,]+)", text)
    if range_match:
        low = int(range_match.group(1).replace(",", ""))
        high = int(range_match.group(2).replace(",", ""))
        return low, f"${range_match.group(1)} - ${range_match.group(2)}"

    # Single price
    single_match = re.search(r"\$([0-9,]+)", text)
    if single_match:
        return int(single_match.group(1).replace(",", "")), None

    return None, None


def _parse_beds_baths(text: str) -> dict:
    """Extract beds/baths from text."""
    result: dict = {}
    bd_match = re.search(r"(\d+)\s*(?:bd|bed|bds|bedroom|BR)\b", text, re.IGNORECASE)
    if bd_match:
        result["bedrooms"] = int(bd_match.group(1))
    elif re.search(r"studio", text, re.IGNORECASE):
        result["bedrooms"] = 0

    ba_match = re.search(r"(\d+\.?\d*)\s*(?:ba|bath|BA)\b", text, re.IGNORECASE)
    if ba_match:
        result["bathrooms"] = float(ba_match.group(1))

    return result


def _parse_sqft(text: str) -> int | None:
    """Extract square footage."""
    match = re.search(r"(\d[0-9,]*)\s*(?:sq\s*ft|sqft)", text, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _extract_zip(text: str) -> str | None:
    """Extract a Chicago zip code."""
    match = re.search(r"\b(606\d{2})\b", text)
    return match.group(1) if match else None


def parse_apartments_email(html: str) -> list[RawListing]:
    """Parse an Apartments.com alert email and extract listings.

    Args:
        html: Raw HTML body of the email.

    Returns:
        List of RawListing objects.
    """
    soup = BeautifulSoup(html, "lxml")
    listings: list[RawListing] = []

    links = soup.find_all("a", href=True)
    listing_links: list[dict] = []

    for link in links:
        href = link.get("href", "")
        if "apartments.com" not in href:
            continue

        listing_id = _extract_listing_id(href)
        if not listing_id:
            continue

        if any(l["listing_id"] == listing_id for l in listing_links):
            continue

        # Walk up to find the containing card
        container = link
        for _ in range(8):
            parent = container.parent
            if parent is None:
                break
            container = parent
            container_text = container.get_text(" ", strip=True)
            if "$" in container_text:
                break

        listing_links.append({
            "listing_id": listing_id,
            "url": href,
            "text": container.get_text(" ", strip=True),
        })

    for item in listing_links:
        try:
            text = item["text"]

            price, price_range = _parse_price_range(text)
            beds_baths = _parse_beds_baths(text)
            sqft = _parse_sqft(text)

            address = None
            address_match = re.search(
                r"(\d+\s+[A-Za-z0-9\s.]+(?:St|Ave|Blvd|Dr|Rd|Ln|Ct|Way|Pl|Pkwy|Ter)[^,]*),?\s*(?:Chicago|CHI)",
                text, re.IGNORECASE,
            )
            if address_match:
                address = address_match.group(1).strip()

            zip_code = _extract_zip(text)
            neighborhood = zip_to_neighborhood(zip_code) if zip_code else None

            extras = {}
            if price_range:
                extras["price_range"] = price_range

            listing = RawListing(
                external_id=item["listing_id"],
                source="apartments_com",
                url=item["url"],
                title=address,
                address=address,
                zip_code=zip_code,
                neighborhood=neighborhood,
                price=price,
                bedrooms=beds_baths.get("bedrooms"),
                bathrooms=beds_baths.get("bathrooms"),
                sqft=sqft,
                extras=extras,
            )
            listings.append(listing)
            logger.debug("apartments_listing_parsed", listing_id=item["listing_id"], price=price)

        except Exception as e:
            logger.warning("apartments_card_parse_error", listing_id=item.get("listing_id"), error=str(e))

    logger.info("apartments_email_parsed", listings_found=len(listings))
    return listings
