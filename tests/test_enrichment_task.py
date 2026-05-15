"""Tests for the enrich_listing and backfill_unenriched Celery tasks."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.enrichment import EnrichmentResult
from app.models.listing import Listing
from app.models.preference import Preference
from app.services.enrichment import EnrichmentOutput


# ── Helpers ────────────────────────────────────────────────────────────────────

GOOD_OUTPUT = EnrichmentOutput(
    amenities={"laundry": "in_unit", "dishwasher": True},
    preference_score=0.75,
    llm_notes="Good overall match.",
    tokens_used=350,
)


def _add_preference(db_session, config=None):
    """Insert an active Preference row so tasks don't bail out early."""
    cfg = config or {
        "pricing": {"max_price": 2200, "alert_threshold": 0.15},
        "unit": {"min_bedrooms": 1, "max_bedrooms": 2, "min_sqft": 500},
        "amenities": {"required": ["in_unit_laundry"], "preferred": ["dishwasher"], "dealbreakers": []},
        "fit": {"vibe": "modern walkable", "building_type": "modern", "transit": []},
        "scoring": {"weights": {"deal": 0.6, "preference": 0.4}, "min_composite_score": 0.55},
        "alerts": {"cooldown_hours": 24, "max_per_day": 100},
    }
    pref = Preference(version=1, config=cfg, active=True)
    db_session.add(pref)
    db_session.flush()
    return pref


def _run_enrich_listing(listing_id: str, db_session, mock_service=None):
    """Run enrich_listing logic directly (bypassing Celery broker) using a patched session."""
    from app.workers.enrichment_tasks import enrich_listing

    # Patch SessionLocal to return our test session
    with patch("app.workers.enrichment_tasks.SessionLocal", return_value=db_session), \
         patch("app.workers.enrichment_tasks.ANTHROPIC_API_KEY", "fake-key"), \
         patch.object(db_session, "close"):  # prevent session close mid-test
        if mock_service:
            with patch("app.workers.enrichment_tasks.EnrichmentService", return_value=mock_service):
                return enrich_listing.run(listing_id)
        else:
            return enrich_listing.run(listing_id)


# ── listing_not_found ──────────────────────────────────────────────────────────

class TestEnrichListingNotFound:

    def test_returns_skipped_when_listing_missing(self, db_session):
        result = _run_enrich_listing(str(uuid.uuid4()), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "not_found"


# ── already enriched ───────────────────────────────────────────────────────────

class TestAlreadyEnriched:

    def test_returns_skipped_when_enrichment_exists(self, db_session, listing_factory, enrichment_factory):
        listing = listing_factory()
        enrichment_factory(listing)

        result = _run_enrich_listing(str(listing.id), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "already_enriched"


# ── enrichment_failed status ───────────────────────────────────────────────────

class TestEnrichmentFailedStatus:

    def test_returns_skipped_when_status_is_enrichment_failed(self, db_session, listing_factory):
        listing = listing_factory(status="enrichment_failed")

        result = _run_enrich_listing(str(listing.id), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "previously_failed"


# ── preference range pre-filter ────────────────────────────────────────────────

class TestPreferenceRangePreFilter:

    def test_price_above_max_is_skipped(self, db_session, listing_factory):
        _add_preference(db_session)
        listing = listing_factory(price=3000, bedrooms=1)  # max_price=2200

        result = _run_enrich_listing(str(listing.id), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "out_of_range"
        enrichment = db_session.query(EnrichmentResult).filter_by(listing_id=listing.id).first()
        assert enrichment is not None
        assert enrichment.skipped is True

    def test_bedrooms_below_min_is_skipped(self, db_session, listing_factory):
        _add_preference(db_session)
        listing = listing_factory(price=1500, bedrooms=0)  # min_bedrooms=1

        result = _run_enrich_listing(str(listing.id), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "out_of_range"

    def test_bedrooms_above_max_is_skipped(self, db_session, listing_factory):
        _add_preference(db_session)
        listing = listing_factory(price=1500, bedrooms=5)  # max_bedrooms=2

        result = _run_enrich_listing(str(listing.id), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "out_of_range"


# ── success path ───────────────────────────────────────────────────────────────

class TestEnrichListingSuccess:

    def test_success_saves_enrichment_result(self, db_session, listing_factory):
        _add_preference(db_session)
        listing = listing_factory(price=1500, bedrooms=1)

        mock_service = MagicMock()
        mock_service.enrich.return_value = GOOD_OUTPUT

        with patch("app.workers.enrichment_tasks.score_new_listing") as mock_score:
            result = _run_enrich_listing(str(listing.id), db_session, mock_service=mock_service)

        assert result["status"] == "enriched"
        assert result["preference_score"] == 0.75
        assert result["tokens_used"] == 350

        enrichment = db_session.query(EnrichmentResult).filter_by(listing_id=listing.id).first()
        assert enrichment is not None
        assert enrichment.skipped is False
        assert enrichment.failed is False
        assert float(enrichment.preference_score) == pytest.approx(0.75)

    def test_success_queues_scoring_task(self, db_session, listing_factory):
        _add_preference(db_session)
        listing = listing_factory(price=1500, bedrooms=1)

        mock_service = MagicMock()
        mock_service.enrich.return_value = GOOD_OUTPUT

        with patch("app.workers.enrichment_tasks.score_new_listing") as mock_score:
            _run_enrich_listing(str(listing.id), db_session, mock_service=mock_service)
            mock_score.delay.assert_called_once_with(str(listing.id))


# ── no active preferences ──────────────────────────────────────────────────────

class TestNoActivePreferences:

    def test_returns_error_when_no_preferences(self, db_session, listing_factory):
        listing = listing_factory(price=1500, bedrooms=1)

        result = _run_enrich_listing(str(listing.id), db_session)

        assert result["status"] == "error"
        assert result["reason"] == "no_preferences"


# ── backfill_unenriched ────────────────────────────────────────────────────────

class TestBackfillUnenriched:

    def test_backfill_dispatches_unenriched_listings(self, db_session, listing_factory):
        listing_factory()
        listing_factory()

        from app.workers.enrichment_tasks import backfill_unenriched

        with patch("app.workers.enrichment_tasks.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"), \
             patch("app.workers.enrichment_tasks.enrich_listing") as mock_enrich:
            result = backfill_unenriched.run()

        assert result["dispatched"] == 2
        assert mock_enrich.delay.call_count == 2

    def test_backfill_skips_enrichment_failed_listings(self, db_session, listing_factory):
        listing_factory(status="enrichment_failed")
        listing_factory()  # normal listing

        from app.workers.enrichment_tasks import backfill_unenriched

        with patch("app.workers.enrichment_tasks.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"), \
             patch("app.workers.enrichment_tasks.enrich_listing") as mock_enrich:
            result = backfill_unenriched.run()

        assert result["dispatched"] == 1

    def test_backfill_skips_already_enriched(self, db_session, listing_factory, enrichment_factory):
        listing = listing_factory()
        enrichment_factory(listing)
        listing_factory()  # unenriched

        from app.workers.enrichment_tasks import backfill_unenriched

        with patch("app.workers.enrichment_tasks.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"), \
             patch("app.workers.enrichment_tasks.enrich_listing") as mock_enrich:
            result = backfill_unenriched.run()

        assert result["dispatched"] == 1

    def test_backfill_respects_limit_of_50(self, db_session, listing_factory):
        for _ in range(60):
            listing_factory()

        from app.workers.enrichment_tasks import backfill_unenriched

        with patch("app.workers.enrichment_tasks.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"), \
             patch("app.workers.enrichment_tasks.enrich_listing") as mock_enrich:
            result = backfill_unenriched.run()

        assert result["dispatched"] == 50
