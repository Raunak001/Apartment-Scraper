"""Tests for resolve_dedup_pairs and run_dedup_sweep Celery tasks."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.dedup import DedupPair
from app.models.listing import Listing


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_pair(db_session, listing_a, listing_b, status="pending", similarity_score=90.0,
               price_diff_pct=5.0):
    pair = DedupPair(
        listing_a_id=listing_a.id,
        listing_b_id=listing_b.id,
        similarity_score=similarity_score,
        price_diff_pct=price_diff_pct,
        status=status,
    )
    db_session.add(pair)
    db_session.flush()
    return pair


def _run_resolve(db_session, mock_resolve_pair=None):
    from app.workers.dedup_tasks import resolve_dedup_pairs

    with patch("app.workers.dedup_tasks.SessionLocal", return_value=db_session), \
         patch.object(db_session, "close"):
        if mock_resolve_pair is not None:
            with patch("app.workers.dedup_tasks.resolve_pair", mock_resolve_pair):
                return resolve_dedup_pairs.run()
        else:
            return resolve_dedup_pairs.run()


# ── No pending pairs ───────────────────────────────────────────────────────────

class TestNoPendingPairs:

    def test_returns_zero_counts_when_no_pairs(self, db_session):
        result = _run_resolve(db_session)

        assert result == {"resolved": 0, "not_duplicate": 0, "merged": 0, "low_confidence": 0}


# ── High-confidence duplicate ──────────────────────────────────────────────────

class TestHighConfidenceDuplicate:

    def test_marks_more_expensive_listing_as_duplicate(self, db_session, listing_factory):
        cheap = listing_factory(price=1400, source="craigslist")
        expensive = listing_factory(price=1600, source="domu")
        _make_pair(db_session, cheap, expensive)

        resolve_return = {"is_duplicate": True, "confidence": 0.90, "reasoning": "Same address"}

        result = _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        db_session.refresh(cheap)
        db_session.refresh(expensive)

        assert cheap.status == "active"
        assert expensive.status == "dedup_duplicate"
        assert result["merged"] == 1

    def test_keeps_cheaper_when_listing_b_is_cheaper(self, db_session, listing_factory):
        expensive = listing_factory(price=1800, source="craigslist")
        cheap = listing_factory(price=1300, source="domu")
        _make_pair(db_session, expensive, cheap)

        resolve_return = {"is_duplicate": True, "confidence": 0.92, "reasoning": "Same unit"}

        result = _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        db_session.refresh(expensive)
        db_session.refresh(cheap)

        assert cheap.status == "active"
        assert expensive.status == "dedup_duplicate"
        assert result["merged"] == 1

    def test_pair_status_set_to_llm_resolved(self, db_session, listing_factory):
        a = listing_factory(price=1400, source="craigslist")
        b = listing_factory(price=1600, source="domu")
        pair = _make_pair(db_session, a, b)

        resolve_return = {"is_duplicate": True, "confidence": 0.85, "reasoning": "Match"}
        _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        db_session.refresh(pair)
        assert pair.status == "llm_resolved"
        assert pair.resolved_at is not None


# ── High-confidence not-duplicate ──────────────────────────────────────────────

class TestHighConfidenceNotDuplicate:

    def test_marks_pair_as_not_duplicate(self, db_session, listing_factory):
        a = listing_factory(price=1400, source="craigslist")
        b = listing_factory(price=1500, source="domu")
        pair = _make_pair(db_session, a, b)

        resolve_return = {"is_duplicate": False, "confidence": 0.88, "reasoning": "Different units"}

        _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        db_session.refresh(a)
        db_session.refresh(b)
        db_session.refresh(pair)

        assert a.status == "active"
        assert b.status == "active"
        assert pair.status == "llm_resolved"
        assert pair.resolution == "not_duplicate"

    def test_not_duplicate_counted_correctly(self, db_session, listing_factory):
        a = listing_factory(price=1400, source="craigslist")
        b = listing_factory(price=1500, source="domu")
        _make_pair(db_session, a, b)

        resolve_return = {"is_duplicate": False, "confidence": 0.80, "reasoning": "Different address"}

        result = _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        assert result["not_duplicate"] == 1
        assert result["merged"] == 0


# ── Low confidence ─────────────────────────────────────────────────────────────

class TestLowConfidence:

    def test_low_confidence_pair_left_pending(self, db_session, listing_factory):
        a = listing_factory(price=1400, source="craigslist")
        b = listing_factory(price=1500, source="domu")
        pair = _make_pair(db_session, a, b)

        resolve_return = {"is_duplicate": True, "confidence": 0.50, "reasoning": "Uncertain"}

        result = _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        db_session.refresh(pair)
        assert pair.status == "pending"  # still pending
        assert result["low_confidence"] == 1
        assert result["resolved"] == 0

    def test_confidence_exactly_at_boundary_075_triggers_merge(self, db_session, listing_factory):
        """Confidence of exactly 0.75 should trigger the merge path."""
        a = listing_factory(price=1300, source="craigslist")
        b = listing_factory(price=1500, source="domu")
        _make_pair(db_session, a, b)

        resolve_return = {"is_duplicate": True, "confidence": 0.75, "reasoning": "Boundary case"}

        result = _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        assert result["merged"] == 1

    def test_confidence_just_below_boundary_stays_pending(self, db_session, listing_factory):
        """Confidence of 0.74 should NOT trigger merge."""
        a = listing_factory(price=1300, source="craigslist")
        b = listing_factory(price=1500, source="domu")
        pair = _make_pair(db_session, a, b)

        resolve_return = {"is_duplicate": True, "confidence": 0.74, "reasoning": "Below boundary"}

        result = _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        db_session.refresh(pair)
        assert pair.status == "pending"
        assert result["low_confidence"] == 1


# ── Deleted listing handling ───────────────────────────────────────────────────

class TestDeletedListing:

    def test_pair_dismissed_when_listing_deleted(self, db_session, listing_factory):
        a = listing_factory(price=1400, source="craigslist")
        b = listing_factory(price=1500, source="domu")
        pair = _make_pair(db_session, a, b)

        # Simulate listing_b being deleted by making db.get return None for it
        original_get = db_session.get

        def mock_get(model, pk):
            if model == Listing and pk == b.id:
                return None
            return original_get(model, pk)

        with patch("app.workers.dedup_tasks.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"), \
             patch.object(db_session, "get", side_effect=mock_get):
            from app.workers.dedup_tasks import resolve_dedup_pairs
            result = resolve_dedup_pairs.run()

        db_session.refresh(pair)
        assert pair.status == "dismissed"
        assert pair.resolution == "listing_deleted"
        assert result["resolved"] == 1


# ── Null price handling ────────────────────────────────────────────────────────

class TestNullPriceHandling:

    def test_null_price_on_both_listings_treated_as_inf(self, db_session, listing_factory):
        """When both prices are None, float('inf') comparison should not crash."""
        a = listing_factory(price=None, source="craigslist")
        b = listing_factory(price=None, source="domu")
        _make_pair(db_session, a, b)

        resolve_return = {"is_duplicate": True, "confidence": 0.90, "reasoning": "Same"}

        # Should not raise; one listing gets marked dedup_duplicate
        result = _run_resolve(db_session, mock_resolve_pair=MagicMock(return_value=resolve_return))

        assert result["merged"] == 1


# ── run_dedup_sweep ────────────────────────────────────────────────────────────

class TestRunDedupSweep:

    def test_sweep_delegates_to_service(self, db_session):
        from app.workers.dedup_tasks import run_dedup_sweep

        mock_result = {"checked": 5, "pairs_created": 2}

        with patch("app.workers.dedup_tasks.SessionLocal", return_value=db_session), \
             patch.object(db_session, "close"), \
             patch("app.workers.dedup_tasks.cross_source_dedup_sweep", return_value=mock_result):
            result = run_dedup_sweep.run()

        assert result == mock_result
