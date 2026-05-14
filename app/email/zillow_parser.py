"""Parse Zillow email alerts into RawListing objects.

Zillow sends HTML emails when saved searches match new listings.
Each email can contain multiple listing cards. This parser extracts
structured data from those cards.
"""

import re

from bs4 import BeautifulSoup

from app.core.logging import get_logger
from app.core.neighborhoods import zip_to_neighborhood
from app.scrapers.base import RawListing

logger = get_logger(__name__)

ZILLOW_SENDER = "noreply@zillow.com"


def _extract_zpid(url: str) -> str | None:
    """Extract the Zillow property ID (zpid) from a listing URL."""
    match = re.search(r"/(\d{6,12})_zpid", url)
    if match:
        return match.group(1)
    match = re.search(r"zpid[=/](\d+)", url)
    if match:
        return match.group(1)
    return None


def _parse_price(text: str) -> int | None:
    """Parse a price string like '$1,200' or '$1,200/mo' or '$1,200+' into an integer."""
    match = re.search(r"\$([0-9,]+)", text)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _parse_beds_baths(text: str) -> dict:
    """Extract beds/baths from text like '2 bd | 1 ba' or '2 bds, 1 ba'."""
    result: dict = {}
    bd_match = re.search(r"(\d+)\s*(?:bd|bed|bds|bedroom)", text, re.IGNORECASE)
    if bd_match:
        result["bedrooms"] = int(bd_match.group(1))
    elif re.search(r"studio", text, re.IGNORECASE):
        result["bedrooms"] = 0

    ba_match = re.search(r"(\d+\.?\d*)\s*(?:ba|bath)", text, re.IGNORECASE)
    if ba_match:
        result["bathrooms"] = float(ba_match.group(1))

    return result


def _parse_sqft(text: str) -> int | None:
    """Extract square footage from text like '800 sqft' or '1,200 sq ft'."""
    match = re.search(r"(\d[0-9,]*)\s*(?:sq\s*ft|sqft)", text, re.IGNORECASE)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def _extract_zip(address: str) -> str | None:
    """Extract a Chicago zip code from an address string."""
    match = re.search(r"\b(606\d{2})\b", address)
    return match.group(1) if match else None


def parse_zillow_email(html: str) -> list[RawListing]:
    """Parse a Zillow alert email and extract listings.

    Args:
        html: Raw HTML body of the email.

    Returns:
        List of RawListing objects. Empty list if parsing fails entirely.
    """
    soup = BeautifulSoup(html, "lxml")
    listings: list[RawListing] = []

    # Zillow emails use table-based layouts with listing cards.
    # Each card typically contains an image, address, price, and details link.
    links = soup.find_all("a", href=True)
    listing_links: list[dict] = []

    for link in links:
        href = link.get("href", "")
        zpid = _extract_zpid(href)
        if not zpid:
            continue

        # Avoid duplicate zpids within the same email
        if any(l["zpid"] == zpid for l in listing_links):
            continue

        # Find the parent container that holds listing data
        container = link
        for _ in range(8):
            parent = container.parent
            if parent is None:
                break
            container = parent
            container_text = container.get_text(" ", strip=True)
            if "$" in container_text and any(kw in container_text.lower() for kw in ["bd", "ba", "bed", "bath", "studio"]):
                break

        container_text = container.get_text(" ", strip=True)
        listing_links.append({
            "zpid": zpid,
            "url": href,
            "text": container_text,
        })

    for item in listing_links:
        try:
            text = item["text"]

            price = _parse_price(text)
            beds_baths = _parse_beds_baths(text)
            sqft = _parse_sqft(text)

            # Try to extract address — usually the most prominent text
            address = None
            address_match = re.search(r"(\d+\s+[A-Za-z0-9\s.]+(?:St|Ave|Blvd|Dr|Rd|Ln|Ct|Way|Pl|Pkwy|Ter)[^,]*),?\s*(?:Chicago|CHI)", text, re.IGNORECASE)
            if address_match:
                address = address_match.group(1).strip()

            zip_code = _extract_zip(text)
            neighborhood = zip_to_neighborhood(zip_code) if zip_code else None

            listing = RawListing(
                external_id=item["zpid"],
                source="zillow",
                url=item["url"],
                title=address,
                address=address,
                zip_code=zip_code,
                neighborhood=neighborhood,
                price=price,
                bedrooms=beds_baths.get("bedrooms"),
                bathrooms=beds_baths.get("bathrooms"),
                sqft=sqft,
            )
            listings.append(listing)
            logger.debug("zillow_listing_parsed", zpid=item["zpid"], price=price)

        except Exception as e:
            logger.warning("zillow_card_parse_error", zpid=item.get("zpid"), error=str(e))

    logger.info("zillow_email_parsed", listings_found=len(listings))
    return listings
