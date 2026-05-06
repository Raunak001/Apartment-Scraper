"""Tests for the Craigslist scraper parsing logic.

These tests verify the parsing helpers without making any HTTP requests.
"""

from app.scrapers.craigslist import (
    _extract_external_id,
    _extract_price_from_title,
    _parse_housing_attrs,
    _parse_price,
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


class TestExtractPriceFromTitle:
    def test_standard_title(self):
        assert _extract_price_from_title("$1,200 / 2br - Nice place") == 1200

    def test_no_price(self):
        assert _extract_price_from_title("Nice apartment in LP") is None

    def test_large_price(self):
        assert _extract_price_from_title("$2,500 / 1br - Luxury") == 2500

    def test_price_no_comma(self):
        assert _extract_price_from_title("$900 / studio") == 900


class TestParsePrice:
    def test_standard(self):
        assert _parse_price("$1,500") == 1500

    def test_no_comma(self):
        assert _parse_price("$800") == 800

    def test_no_dollar_sign(self):
        assert _parse_price("no price here") is None


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
