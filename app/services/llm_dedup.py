"""Stage-2 LLM-powered deduplication via Claude Haiku.

Resolves ambiguous DedupPair entries where Stage-1 fuzzy matching flagged
a probable match but price difference was too large for auto-merge.
"""

import json
from datetime import datetime, timezone

import anthropic

from app.core.config import ANTHROPIC_API_KEY
from app.core.logging import get_logger

logger = get_logger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_PAIRS_PER_RUN = 20

DEDUP_SYSTEM = (
    "You are a duplicate apartment listing detector. Given two listings from "
    "different sources, determine if they refer to the same physical apartment. "
    "Return ONLY valid JSON, no markdown fences or extra text."
)

DEDUP_USER = """Listing A (source: {source_a}):
  Address: {address_a}
  Unit: {unit_a}
  Price: ${price_a}/month
  Bedrooms: {beds_a} | Bathrooms: {baths_a} | Sqft: {sqft_a}
  Description (excerpt): {desc_a}

Listing B (source: {source_b}):
  Address: {address_b}
  Unit: {unit_b}
  Price: ${price_b}/month
  Bedrooms: {beds_b} | Bathrooms: {baths_b} | Sqft: {sqft_b}
  Description (excerpt): {desc_b}

Analyze whether these are the same physical apartment. Consider:
- Address similarity (abbreviations, formatting differences)
- Unit number match
- Price difference (same unit may be listed at different prices)
- Bedroom/bathroom count match
- Description overlap

Return JSON:
{{
  "is_duplicate": true | false,
  "confidence": 0.0 to 1.0,
  "reasoning": "brief explanation"
}}"""


def resolve_pair(listing_a, listing_b) -> dict:
    """Use Claude Haiku to determine if two listings are duplicates.

    Args:
        listing_a: Listing ORM object
        listing_b: Listing ORM object

    Returns:
        Dict with keys: is_duplicate, confidence, reasoning, tokens_used
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    desc_a = (listing_a.description or "")[:500]
    desc_b = (listing_b.description or "")[:500]

    prompt = DEDUP_USER.format(
        source_a=listing_a.source,
        address_a=listing_a.address or "N/A",
        unit_a=listing_a.unit_number or "N/A",
        price_a=listing_a.price or "N/A",
        beds_a=listing_a.bedrooms if listing_a.bedrooms is not None else "N/A",
        baths_a=listing_a.bathrooms if listing_a.bathrooms is not None else "N/A",
        sqft_a=listing_a.sqft or "N/A",
        desc_a=desc_a or "N/A",
        source_b=listing_b.source,
        address_b=listing_b.address or "N/A",
        unit_b=listing_b.unit_number or "N/A",
        price_b=listing_b.price or "N/A",
        beds_b=listing_b.bedrooms if listing_b.bedrooms is not None else "N/A",
        baths_b=listing_b.bathrooms if listing_b.bathrooms is not None else "N/A",
        sqft_b=listing_b.sqft or "N/A",
        desc_b=desc_b or "N/A",
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=DEDUP_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        tokens_used = (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)

        # Parse JSON response
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()

        result = json.loads(text)
        result["tokens_used"] = tokens_used

        logger.info(
            "llm_dedup_resolved",
            listing_a=str(listing_a.id),
            listing_b=str(listing_b.id),
            is_duplicate=result.get("is_duplicate"),
            confidence=result.get("confidence"),
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("llm_dedup_json_error", error=str(e), raw_text=text[:200])
        return {"is_duplicate": False, "confidence": 0.0, "reasoning": "JSON parse error", "tokens_used": 0}
    except Exception as e:
        logger.error("llm_dedup_error", error=str(e))
        return {"is_duplicate": False, "confidence": 0.0, "reasoning": str(e), "tokens_used": 0}
