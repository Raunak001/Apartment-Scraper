"""Tests for score_new_listing, backfill_deal_scores, and rebuild_price_distributions tasks."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.listing import Listing


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run_score_new_listing(listing_id: str, db_session):
    """Run score_new_listing task logic directly via a patched session."""
    from app.workers.pricing_tasks import score_new_listing

    with patch("app.workers.pricing_tasks.SessionLocal", return_value=db_session), \
         patch.object(db_session, "close"):
        return score_new_listing.run(listing_id)


def _run_backfill_deal_scores(db_session):
    from app.workers.pricing_tasks import backfill_deal_scores

    with patch("app.workers.pricing_tasks.SessionLocal", return_value=db_session), \
         patch.object(db_session, "close"), \
         patch("app.workers.pricing_tasks.score_new_listing") as mock_task:
        result = backfill_deal_scores.run()
        return result, mock_task


def _run_rebuild(db_session):
    from app.workers.pricing_tasks import rebuild_price_distributions

    with patch("app.workers.pricing_tasks.SessionLocal", return_value=db_session), \
         patch.object(db_session, "close"):
        return rebuild_price_distributions.run()


# ── score_new_listing ──────────────────────────────────────────────────────────

class TestScoreNewListing:

    def test_listing_not_found_returns_skipped(self, db_session):
        result = _run_score_new_listing(str(uuid.uuid4()), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "not_found"

    def test_no_enrichment_returns_skipped(self, db_session, listing_factory):
        listing = listing_factory()

        result = _run_score_new_listing(str(listing.id), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "no_enrichment"

    def test_enrichment_skipped_returns_skipped(self, db_session, listing_factory, enrichment_factory):
        listing = listing_factory()
        enrichment_factory(listing, skipped=True)

        result = _run_score_new_listing(str(listing.id), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "pre_filtered"

    def test_enrichment_failed_returns_skipped(self, db_session, listing_factory, enrichment_factory):
        listing = listing_factory()
        enrichment_factory(listing, failed=True)

        result = _run_score_new_listing(str(listing.id), db_session)

        assert result["status"] == "skipped"
        assert result["reason"] == "enrichment_failed"

    def test_no_distribution_saves_none_and_no_alert(
        self, db_session, listing_factory, enrichment_factory
    ):
        listing = listing_factory(price=1500, neighborhood="wicker_park", bedrooms=2)
        enrichment_factory(listing)

        with patch("app.workers.pricing_tasks.evaluate_single_listing") as mock_eval:
            result = _run_score_new_listing(str(listing.id), db_session)

        assert result["status"] == "scored"
        assert result["deal_score"] is None
        mock_eval.delay.assert_not_called()

        db_session.refresh(listing)
        assert listing.deal_score is None

    def test_successful_score_saves_deal_score_and_queues_alert(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1)
        enrichment_factory(listing)

        with patch("app.workers.pricing_tasks.evaluate_single_listing") as mock_eval:
            result = _run_score_new_listing(str(listing.id), db_session)

        assert result["status"] == "scored"
        assert result["deal_score"] is not None
        assert result["deal_score"] > 0.5
        mock_eval.delay.assert_called_once_with(str(listing.id))

        db_session.refresh(listing)
        assert listing.deal_score is not None


# ── backfill_deal_scores ───────────────────────────────────────────────────────

class TestBackfillDealScores:

    def test_backfill_dispatches_unscoredlistings(self, db_session, listing_factory, enrichment_factory):
        listing = listing_factory(deal_score=None)
        enrichment_factory(listing)

        result, mock_task = _run_backfill_deal_scores(db_session)

        assert result["dispatched"] == 1
        mock_task.delay.assert_called_once_with(str(listing.id))

    def test_backfill_skips_already_scored_listings(self, db_session, listing_factory, enrichment_factory):
        listing = listing_factory(deal_score=0.75)
        enrichment_factory(listing)

        result, mock_task = _run_backfill_deal_scores(db_session)

        assert result["dispatched"] == 0

    def test_backfill_skips_skipped_enrichment(self, db_session, listing_factory, enrichment_factory):
        listing = listing_factory(deal_score=None)
        enrichment_factory(listing, skipped=True)

        result, mock_task = _run_backfill_deal_scores(db_session)

        assert result["dispatched"] == 0

    def test_backfill_skips_failed_enrichment(self, db_session, listing_factory, enrichment_factory):
        listing = listing_factory(deal_score=None)
        enrichment_factory(listing, failed=True)

        result, mock_task = _run_backfill_deal_scores(db_session)

        assert result["dispatched"] == 0

    def test_backfill_dispatches_multiple_listings(self, db_session, listing_factory, enrichment_factory):
        for _ in range(5):
            l = listing_factory(deal_score=None)
            enrichment_factory(l)

        result, mock_task = _run_backfill_deal_scores(db_session)

        assert result["dispatched"] == 5
        assert mock_task.delay.call_count == 5


# ── rebuild_price_distributions task ──────────────────────────────────────────

class TestRebuildPriceDistributionsTask:

    def test_task_runs_successfully_on_empty_db(self, db_session):
        result = _run_rebuild(db_session)

        assert result["segments_updated"] == 0
        assert result["total_sampled"] == 0

    def test_task_returns_correct_counts_with_data(self, db_session, listing_factory):
        listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1, status="active")
        listing_factory(price=2000, neighborhood="wicker_park", bedrooms=2, status="active")

        result = _run_rebuild(db_session)

        assert result["segments_updated"] == 2
        assert result["total_sampled"] == 2
