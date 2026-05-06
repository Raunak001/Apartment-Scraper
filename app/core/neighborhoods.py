"""Chicago zip code to neighborhood mapping.

Used to assign a neighborhood to a listing based on the zip code
extracted from the listing's location data.
"""

# Mapping of Chicago zip codes to the neighborhood names used in preferences.yaml.
# A zip code can span multiple neighborhoods — we pick the dominant one.
# Source: USPS + Chicago neighborhood boundaries.
ZIP_TO_NEIGHBORHOOD: dict[str, str] = {
    # Lincoln Park
    "60614": "lincoln_park",
    "60657": "lincoln_park",  # also Lakeview — LP is dominant for southern 60657

    # Wicker Park / Ukrainian Village
    "60622": "wicker_park",

    # Logan Square
    "60647": "logan_square",

    # Bucktown (shares 60647 with Logan Square — Bucktown is east of the Kennedy)
    # Listings in 60647 default to logan_square; Bucktown detection may need
    # address-level refinement in a future phase.

    # Roscoe Village
    "60618": "roscoe_village",
}

# Reverse lookup: which zip codes are relevant per neighborhood
NEIGHBORHOOD_ZIPS: dict[str, list[str]] = {}
for _zip, _hood in ZIP_TO_NEIGHBORHOOD.items():
    NEIGHBORHOOD_ZIPS.setdefault(_hood, []).append(_zip)


def zip_to_neighborhood(zip_code: str | None) -> str | None:
    """Return the neighborhood name for a Chicago zip code, or None if unmapped."""
    if not zip_code:
        return None
    # Handle 9-digit zip codes (e.g., 60614-1234)
    zip5 = zip_code.strip()[:5]
    return ZIP_TO_NEIGHBORHOOD.get(zip5)
