"""Tests for EnrichmentService — LLM amenity extraction and preference scoring."""

import json
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from app.services.enrichment import EnrichmentService


# ── Helpers ────────────────────────────────────────────────────────────────────

AMENITY_RESPONSE = json.dumps({
    "laundry": "in_unit",
    "dishwasher": True,
    "parking": "none",
    "air_conditioning": "central",
    "pets": "cats_ok",
    "outdoor_space": "balcony",
    "building_type": "modern",
    "transit_proximity": "2 blocks from Blue Line",
    "notable_features": ["hardwood floors"],
    "red_flags": [],
})

SCORE_RESPONSE = json.dumps({"preference_score": 0.82, "reasoning": "Great match on amenities and transit."})


def _make_listing(
    title="Nice 1BR",
    price=1500,
    bedrooms=1,
    bathrooms=1.0,
    sqft=700,
    neighborhood="lincoln_park",
    description="In-unit washer/dryer. Dishwasher. Near Blue Line.",
):
    listing = MagicMock()
    listing.title = title
    listing.price = price
    listing.bedrooms = bedrooms
    listing.bathrooms = bathrooms
    listing.sqft = sqft
    listing.neighborhood = neighborhood
    listing.description = description
    return listing


def _make_response(text, input_tokens=100, output_tokens=50):
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage.input_tokens = input_tokens
    resp.usage.output_tokens = output_tokens
    return resp


SAMPLE_PREFS = {
    "amenities": {"required": ["in_unit_laundry"], "preferred": ["dishwasher"], "dealbreakers": []},
    "fit": {"vibe": "Modern walkable", "building_type": "modern", "transit": []},
    "unit": {"min_bedrooms": 1, "max_bedrooms": 2, "min_sqft": 500},
}


# ── Happy path ─────────────────────────────────────────────────────────────────

class TestEnrichHappyPath:

    def test_enrich_returns_output_with_correct_fields(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE, input_tokens=200, output_tokens=100),
            _make_response(SCORE_RESPONSE, input_tokens=150, output_tokens=60),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            output = service.enrich(_make_listing(), SAMPLE_PREFS)

        assert output.preference_score == pytest.approx(0.82)
        assert output.amenities["laundry"] == "in_unit"
        assert output.amenities["dishwasher"] is True
        assert "Great match" in output.reasoning
        assert output.tokens_used == (200 + 100) + (150 + 60)

    def test_enrich_makes_two_llm_calls(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE),
            _make_response(SCORE_RESPONSE),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            service.enrich(_make_listing(), SAMPLE_PREFS)

        assert mock_anthropic_client.messages.create.call_count == 2

    def test_enrich_clamps_preference_score_above_1(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE),
            _make_response(json.dumps({"preference_score": 1.5, "reasoning": "Too good"})),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            output = service.enrich(_make_listing(), SAMPLE_PREFS)

        assert output.preference_score == 1.0

    def test_enrich_clamps_preference_score_below_0(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE),
            _make_response(json.dumps({"preference_score": -0.5, "reasoning": "Bad"})),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            output = service.enrich(_make_listing(), SAMPLE_PREFS)

        assert output.preference_score == 0.0


# ── Missing / null listing fields ──────────────────────────────────────────────

class TestEnrichMissingFields:

    def test_enrich_listing_with_null_description_still_runs(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE),
            _make_response(SCORE_RESPONSE),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            output = service.enrich(_make_listing(description=None), SAMPLE_PREFS)

        assert output.preference_score == pytest.approx(0.82)

    def test_enrich_listing_with_null_title_still_runs(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE),
            _make_response(SCORE_RESPONSE),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            output = service.enrich(_make_listing(title=None), SAMPLE_PREFS)

        assert output is not None

    def test_enrich_listing_with_null_price_uses_na(self, mock_anthropic_client):
        """Null price should not crash — the prompt uses 'N/A' as fallback."""
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE),
            _make_response(SCORE_RESPONSE),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            output = service.enrich(_make_listing(price=None), SAMPLE_PREFS)

        # Should not raise; first call prompt is built, tokens accumulate
        assert output.tokens_used > 0


# ── JSON parsing robustness ────────────────────────────────────────────────────

class TestParseJsonResponse:

    def setup_method(self):
        with patch("app.services.enrichment.anthropic.Anthropic"):
            self.service = EnrichmentService()

    def test_parse_clean_json(self):
        result = self.service._parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_with_markdown_fences(self):
        text = "```json\n{\"key\": \"value\"}\n```"
        result = self.service._parse_json_response(text)
        assert result == {"key": "value"}

    def test_parse_json_with_leading_prose(self):
        text = 'Here is the output:\n{"key": "value"}\nThank you!'
        result = self.service._parse_json_response(text)
        assert result == {"key": "value"}

    def test_parse_json_with_nested_object(self):
        data = {"laundry": "in_unit", "notable_features": ["hardwood", "exposed brick"]}
        text = json.dumps(data)
        result = self.service._parse_json_response(text)
        assert result["notable_features"] == ["hardwood", "exposed brick"]

    def test_parse_completely_unparseable_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse JSON"):
            self.service._parse_json_response("This is not JSON at all, just text.")

    def test_parse_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            self.service._parse_json_response("")

    def test_parse_truncated_json_raises_value_error(self):
        with pytest.raises(ValueError):
            self.service._parse_json_response('{"key": "val')

    def test_parse_json_with_backticks_no_language_tag(self):
        text = "```\n{\"key\": \"value\"}\n```"
        result = self.service._parse_json_response(text)
        assert result["key"] == "value"


# ── Token counting ─────────────────────────────────────────────────────────────

class TestTokenCounting:

    def test_tokens_accumulate_from_both_calls(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE, input_tokens=300, output_tokens=120),
            _make_response(SCORE_RESPONSE, input_tokens=200, output_tokens=80),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            output = service.enrich(_make_listing(), SAMPLE_PREFS)

        assert output.tokens_used == 300 + 120 + 200 + 80

    def test_tokens_are_positive(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE, input_tokens=1, output_tokens=1),
            _make_response(SCORE_RESPONSE, input_tokens=1, output_tokens=1),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            output = service.enrich(_make_listing(), SAMPLE_PREFS)

        assert output.tokens_used == 4


# ── API error propagation ──────────────────────────────────────────────────────

class TestEnrichApiErrors:

    def test_api_timeout_propagates(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            with pytest.raises(anthropic.APITimeoutError):
                service.enrich(_make_listing(), SAMPLE_PREFS)

    def test_rate_limit_error_propagates(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = anthropic.RateLimitError(
            message="rate limited", response=MagicMock(), body={}
        )

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            with pytest.raises(anthropic.RateLimitError):
                service.enrich(_make_listing(), SAMPLE_PREFS)

    def test_json_parse_failure_on_second_call_raises(self, mock_anthropic_client):
        mock_anthropic_client.messages.create.side_effect = [
            _make_response(AMENITY_RESPONSE),
            _make_response("Not JSON at all"),
        ]

        with patch("app.services.enrichment.anthropic.Anthropic", return_value=mock_anthropic_client):
            service = EnrichmentService()
            with pytest.raises(ValueError):
                service.enrich(_make_listing(), SAMPLE_PREFS)
