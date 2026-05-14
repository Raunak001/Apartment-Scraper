"""Shared data structures for all scrapers and email parsers."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawListing:
    """Intermediate representation of a scraped listing before DB insertion."""

    external_id: str
    source: str
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
