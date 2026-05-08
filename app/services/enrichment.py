"""LLM-powered enrichment: amenity extraction + preference scoring via Claude Haiku."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic

from app.core.config import ANTHROPIC_API_KEY
from app.core.logging import get_logger

logger = get_logger(__name__)

MODEL = "claude-haiku-4-5-20251001"


@dataclass
class EnrichmentOutput:
    amenities: dict
    preference_score: float
    llm_notes: str
    tokens_used: int


AMENITY_EXTRACTION_SYSTEM = (
    "You are an apartment listing analyzer. Extract structured amenities from the "
    "listing description. Return ONLY valid JSON, no markdown fences or extra text."
)

AMENITY_EXTRACTION_USER = """Listing Title: {title}
Price: ${price}/month
Bedrooms: {bedrooms} | Bathrooms: {bathrooms} | Sqft: {sqft}
Neighborhood: {neighborhood}
Description:
{description}

Extract these amenity categories as a JSON object:
{{
  "laundry": "in_unit" | "in_building" | "none" | "unknown",
  "dishwasher": true | false | null,
  "parking": "included" | "available" | "street" | "none" | "unknown",
  "air_conditioning": "central" | "window" | "none" | "unknown",
  "pets": "cats_ok" | "dogs_ok" | "both_ok" | "no_pets" | "unknown",
  "outdoor_space": "balcony" | "patio" | "roof_deck" | "yard" | "none" | "unknown",
  "building_type": "vintage" | "modern" | "mid_century" | "unknown",
  "transit_proximity": "description of nearby transit if mentioned, else null",
  "notable_features": ["list", "of", "other", "standout", "features"],
  "red_flags": ["list", "of", "concerning", "items"]
}}

Only include information explicitly stated or strongly implied. Use null/unknown rather than guessing."""

PREFERENCE_SCORING_SYSTEM = (
    "You are a rental apartment fit scorer. Given a listing's amenities and a renter's "
    "preferences, produce a preference_score from 0.0 to 1.0 with brief reasoning. "
    "Return ONLY valid JSON, no markdown fences or extra text."
)

PREFERENCE_SCORING_USER = """## Listing Summary
Title: {title}
Price: ${price}/month | {bedrooms}BR/{bathrooms}BA | {sqft} sqft
Neighborhood: {neighborhood}

## Extracted Amenities
{amenities_json}

## Renter Preferences
Required amenities: {required}
Preferred amenities: {preferred}
Dealbreakers: {dealbreakers}
Vibe: {vibe}
Building type preference: {building_type}
Transit preferences: {transit}
Unit requirements: {min_bedrooms}-{max_bedrooms} BR, min {min_sqft} sqft

## Scoring Rules
- Score 0.0 if ANY dealbreaker is present
- Score 0.0-0.3 if required amenities are missing
- Score 0.3-0.6 if required amenities present but few preferred matches
- Score 0.6-0.8 if good overall match
- Score 0.8-1.0 if excellent match across all dimensions

Return:
{{"preference_score": <float 0.0-1.0>, "reasoning": "<1-2 sentence explanation>"}}"""


class EnrichmentService:

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def enrich(self, listing, preferences: dict) -> EnrichmentOutput:
        amenities, tokens_1 = self._extract_amenities(listing)
        score, reasoning, tokens_2 = self._score_preferences(listing, amenities, preferences)
        return EnrichmentOutput(
            amenities=amenities,
            preference_score=score,
            llm_notes=reasoning,
            tokens_used=tokens_1 + tokens_2,
        )

    def _extract_amenities(self, listing) -> tuple[dict, int]:
        prompt = AMENITY_EXTRACTION_USER.format(
            title=listing.title or "Unknown",
            price=listing.price or "N/A",
            bedrooms=listing.bedrooms if listing.bedrooms is not None else "N/A",
            bathrooms=listing.bathrooms or "N/A",
            sqft=listing.sqft or "N/A",
            neighborhood=listing.neighborhood or "Unknown",
            description=listing.description or "No description provided.",
        )

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=AMENITY_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        tokens = response.usage.input_tokens + response.usage.output_tokens
        text = response.content[0].text
        amenities = self._parse_json_response(text)
        return amenities, tokens

    def _score_preferences(self, listing, amenities: dict, preferences: dict) -> tuple[float, str, int]:
        amenity_prefs = preferences.get("amenities", {})
        fit = preferences.get("fit", {})
        unit = preferences.get("unit", {})

        prompt = PREFERENCE_SCORING_USER.format(
            title=listing.title or "Unknown",
            price=listing.price or "N/A",
            bedrooms=listing.bedrooms if listing.bedrooms is not None else "N/A",
            bathrooms=listing.bathrooms or "N/A",
            sqft=listing.sqft or "N/A",
            neighborhood=listing.neighborhood or "Unknown",
            amenities_json=json.dumps(amenities, indent=2),
            required=amenity_prefs.get("required", []),
            preferred=amenity_prefs.get("preferred", []),
            dealbreakers=amenity_prefs.get("dealbreakers", []),
            vibe=fit.get("vibe", "No preference"),
            building_type=fit.get("building_type", "no_preference"),
            transit=fit.get("transit", []),
            min_bedrooms=unit.get("min_bedrooms", 1),
            max_bedrooms=unit.get("max_bedrooms", 2),
            min_sqft=unit.get("min_sqft", "none"),
        )

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=PREFERENCE_SCORING_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        tokens = response.usage.input_tokens + response.usage.output_tokens
        text = response.content[0].text
        result = self._parse_json_response(text)

        score = float(result.get("preference_score", 0.0))
        score = max(0.0, min(1.0, score))
        reasoning = result.get("reasoning", "")
        return score, reasoning, tokens

    def _parse_json_response(self, text: str) -> dict:
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strip markdown code fences
        stripped = re.sub(r"```(?:json)?\s*", "", text)
        stripped = re.sub(r"```\s*$", "", stripped).strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # Regex extract first JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        logger.warning("json_parse_failed", raw_text=text[:200])
        raise ValueError(f"Could not parse JSON from LLM response: {text[:100]}")
