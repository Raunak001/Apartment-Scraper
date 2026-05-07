"""Tests for the Craigslist scraper parsing logic.

These tests verify the parsing helpers without making any HTTP requests.
"""

from app.scrapers.craigslist import (
    _extract_external_id,
    _parse_housing_attrs,
    _parse_price,
    _resolve_neighborhood,
)


class TestExtractExternalId:
    def test_standard_url(self):
        url = "https://chicago.craigslist.org/chc/apa/d/chicago-nice-place/7841234567.html"
        assert _extract_external_id(url) == "7841234567"

    def test_url_with_different_area(self):
        url = "https://chicago.craigslist.org/nwc/apa/d/evanston-apartment/7841234567.html"
        assert _extract_external_id(url) == "7841234567"

    def test_no_id_in_url(self):
        assert _extract_external_id("https://chicago.craigslist.org/search/apa") is None

    def test_empty_url(self):
        assert _extract_external_id("") is None


class TestParsePrice:
    def test_standard(self):
        assert _parse_price("$1,500") == 1500

    def test_no_comma(self):
        assert _parse_price("$800") == 800

    def test_no_dollar_sign(self):
        assert _parse_price("no price here") is None

    def test_large_price(self):
        assert _parse_price("$2,500") == 2500


class TestParseHousingAttrs:
    def test_full_attrs(self):
        result = _parse_housing_attrs("2BR / 1Ba 800ft²")
        assert result == {"bedrooms": 2, "bathrooms": 1.0, "sqft": 800}

    def test_bedrooms_only(self):
        result = _parse_housing_attrs("3BR")
        assert result == {"bedrooms": 3}

    def test_half_bath(self):
        result = _parse_housing_attrs("1BR / 1.5Ba")
        assert result == {"bedrooms": 1, "bathrooms": 1.5}

    def test_empty_text(self):
        result = _parse_housing_attrs("")
        assert result == {}

    def test_sqft_without_unicode(self):
        result = _parse_housing_attrs("1000ft2")
        assert result == {"sqft": 1000}

    def test_studio(self):
        result = _parse_housing_attrs("Studio / 1Ba 450ft²")
        assert result == {"bedrooms": 0, "bathrooms": 1.0, "sqft": 450}

    def test_studio_no_other_attrs(self):
        result = _parse_housing_attrs("studio")
        assert result == {"bedrooms": 0}


class TestResolveNeighborhood:
    """Test the two-tier neighborhood resolution: zip code first, then location text."""

    def test_zip_based_neighborhood_preferred(self):
        detail = {"neighborhood": "lincoln_park", "zip_code": "60614"}
        assert _resolve_neighborhood(detail, "West Loop") == "lincoln_park"

    def test_falls_back_to_location_text(self):
        detail = {}  # no zip, no neighborhood
        assert _resolve_neighborhood(detail, "Wicker Park") == "wicker_park"

    def test_location_text_case_insensitive(self):
        detail = {}
        assert _resolve_neighborhood(detail, "LOGAN SQUARE") == "logan_square"

    def test_location_text_old_town(self):
        detail = {}
        assert _resolve_neighborhood(detail, "Old Town") == "old_town"

    def test_location_text_west_loop(self):
        detail = {}
        assert _resolve_neighborhood(detail, "West Loop") == "west_loop"

    def test_location_text_river_north(self):
        detail = {}
        assert _resolve_neighborhood(detail, "River North") == "river_north"

    def test_unknown_location_returns_none(self):
        detail = {}
        assert _resolve_neighborhood(detail, "Naperville") is None

    def test_no_location_returns_none(self):
        detail = {}
        assert _resolve_neighborhood(detail, None) is None
