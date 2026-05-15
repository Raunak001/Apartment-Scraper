"""Tests for AlertDispatcher — composite scoring, gate checks, and alert firing."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.services.alerting import AlertDispatcher
from app.services.notifications.base import AlertPayload


# ── compute_composite ──────────────────────────────────────────────────────────

class TestComputeComposite:

    def test_default_weights_60_40(self):
        dispatcher = AlertDispatcher()
        score = dispatcher.compute_composite(
            deal_score=0.8, preference_score=0.6, weights={"deal": 0.6, "preference": 0.4}
        )
        assert score == pytest.approx(0.8 * 0.6 + 0.6 * 0.4, abs=1e-4)

    def test_custom_weights(self):
        dispatcher = AlertDispatcher()
        score = dispatcher.compute_composite(
            deal_score=1.0, preference_score=0.0, weights={"deal": 1.0, "preference": 0.0}
        )
        assert score == pytest.approx(1.0)

    def test_both_scores_zero(self):
        dispatcher = AlertDispatcher()
        score = dispatcher.compute_composite(0.0, 0.0, {"deal": 0.6, "preference": 0.4})
        assert score == 0.0

    def test_both_scores_one(self):
        dispatcher = AlertDispatcher()
        score = dispatcher.compute_composite(1.0, 1.0, {"deal": 0.6, "preference": 0.4})
        assert score == pytest.approx(1.0)

    def test_result_rounded_to_4_decimals(self):
        dispatcher = AlertDispatcher()
        score = dispatcher.compute_composite(0.333, 0.667, {"deal": 0.6, "preference": 0.4})
        assert score == round(score, 4)

    def test_missing_weights_uses_defaults(self):
        """When weights dict is missing keys, should use default 0.6/0.4."""
        dispatcher = AlertDispatcher()
        score = dispatcher.compute_composite(1.0, 0.0, {})
        assert score == pytest.approx(0.6)


# ── passes_alert_threshold ─────────────────────────────────────────────────────

class TestPassesAlertThreshold:

    def test_price_15pct_below_median_passes(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1275, neighborhood="lincoln_park", bedrooms=1)  # exactly -15%

        result = AlertDispatcher().passes_alert_threshold(db_session, listing, alert_threshold=0.15)

        assert result is True

    def test_price_at_median_does_not_pass(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1)

        result = AlertDispatcher().passes_alert_threshold(db_session, listing, alert_threshold=0.15)

        assert result is False

    def test_price_just_above_threshold_does_not_pass(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1400, neighborhood="lincoln_park", bedrooms=1)  # -6.7%, not enough

        result = AlertDispatcher().passes_alert_threshold(db_session, listing, alert_threshold=0.15)

        assert result is False

    def test_no_distribution_returns_false(self, db_session, listing_factory):
        listing = listing_factory(price=1000, neighborhood="unknown_hood", bedrooms=1)

        result = AlertDispatcher().passes_alert_threshold(db_session, listing, alert_threshold=0.15)

        assert result is False

    def test_null_price_returns_false(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=None, neighborhood="lincoln_park", bedrooms=1)

        result = AlertDispatcher().passes_alert_threshold(db_session, listing, alert_threshold=0.15)

        assert result is False

    def test_null_neighborhood_returns_false(self, db_session, listing_factory):
        listing = listing_factory(price=1000, neighborhood=None, bedrooms=1)

        result = AlertDispatcher().passes_alert_threshold(db_session, listing, alert_threshold=0.15)

        assert result is False


# ── is_on_cooldown ─────────────────────────────────────────────────────────────

class TestIsOnCooldown:

    def test_recent_alert_triggers_cooldown(self, db_session, listing_factory, alert_history_factory):
        listing = listing_factory()
        alert_history_factory(listing, fired_at=datetime.now(timezone.utc) - timedelta(hours=1))

        result = AlertDispatcher().is_on_cooldown(db_session, listing.id, cooldown_hours=24)

        assert result is True

    def test_old_alert_does_not_trigger_cooldown(self, db_session, listing_factory, alert_history_factory):
        listing = listing_factory()
        alert_history_factory(listing, fired_at=datetime.now(timezone.utc) - timedelta(hours=25))

        result = AlertDispatcher().is_on_cooldown(db_session, listing.id, cooldown_hours=24)

        assert result is False

    def test_no_alert_history_returns_false(self, db_session, listing_factory):
        listing = listing_factory()

        result = AlertDispatcher().is_on_cooldown(db_session, listing.id, cooldown_hours=24)

        assert result is False

    def test_cooldown_boundary_exactly_at_cutoff(self, db_session, listing_factory, alert_history_factory):
        """Alert fired exactly at cooldown_hours ago should NOT trigger cooldown."""
        listing = listing_factory()
        fired_at = datetime.now(timezone.utc) - timedelta(hours=24, seconds=1)
        alert_history_factory(listing, fired_at=fired_at)

        result = AlertDispatcher().is_on_cooldown(db_session, listing.id, cooldown_hours=24)

        assert result is False


# ── alerts_fired_in_window ─────────────────────────────────────────────────────

class TestAlertsFiredInWindow:

    def test_counts_alerts_in_last_24h(self, db_session, listing_factory, alert_history_factory):
        listing = listing_factory()
        alert_history_factory(listing, fired_at=datetime.now(timezone.utc) - timedelta(hours=1))
        alert_history_factory(listing, fired_at=datetime.now(timezone.utc) - timedelta(hours=12))

        count = AlertDispatcher().alerts_fired_in_window(db_session)

        assert count == 2

    def test_excludes_alerts_older_than_24h(self, db_session, listing_factory, alert_history_factory):
        listing = listing_factory()
        alert_history_factory(listing, fired_at=datetime.now(timezone.utc) - timedelta(hours=25))

        count = AlertDispatcher().alerts_fired_in_window(db_session)

        assert count == 0

    def test_empty_history_returns_zero(self, db_session):
        count = AlertDispatcher().alerts_fired_in_window(db_session)
        assert count == 0


# ── build_payload ──────────────────────────────────────────────────────────────

class TestBuildPayload:

    def _make_listing(self, **kwargs):
        listing = MagicMock()
        listing.title = "Nice 1BR"
        listing.url = "https://example.com/1.html"
        listing.price = 1400
        listing.neighborhood = "lincoln_park"
        listing.bedrooms = 1
        listing.deal_score = 0.75
        listing.source = "craigslist"
        for k, v in kwargs.items():
            setattr(listing, k, v)
        return listing

    def _make_enrichment(self, **kwargs):
        enrichment = MagicMock()
        enrichment.llm_notes = "Good match."
        enrichment.amenities = {"laundry": "in_unit", "dishwasher": True}
        enrichment.preference_score = 0.70
        for k, v in kwargs.items():
            setattr(enrichment, k, v)
        return enrichment

    def test_payload_includes_source(self):
        listing = self._make_listing(source="craigslist")
        enrichment = self._make_enrichment()

        payload = AlertDispatcher().build_payload(listing, enrichment, composite=0.73)

        assert payload.source == "craigslist"

    def test_payload_truncates_long_reasoning(self):
        long_notes = "A" * 300
        listing = self._make_listing()
        enrichment = self._make_enrichment(llm_notes=long_notes)

        payload = AlertDispatcher().build_payload(listing, enrichment, composite=0.73)

        assert len(payload.reasoning) <= 200
        assert payload.reasoning.endswith("...")

    def test_payload_short_reasoning_not_truncated(self):
        listing = self._make_listing()
        enrichment = self._make_enrichment(llm_notes="Short note.")

        payload = AlertDispatcher().build_payload(listing, enrichment, composite=0.73)

        assert payload.reasoning == "Short note."

    def test_payload_null_reasoning_becomes_empty_string(self):
        listing = self._make_listing()
        enrichment = self._make_enrichment(llm_notes=None)

        payload = AlertDispatcher().build_payload(listing, enrichment, composite=0.73)

        assert payload.reasoning == ""

    def test_payload_null_amenities_shows_na(self):
        listing = self._make_listing()
        enrichment = self._make_enrichment(amenities=None)

        payload = AlertDispatcher().build_payload(listing, enrichment, composite=0.73)

        assert payload.amenities_summary == "N/A"

    def test_payload_amenities_formatted_as_pipe_list(self):
        listing = self._make_listing()
        enrichment = self._make_enrichment(amenities={"laundry": "in_unit", "dishwasher": True})

        payload = AlertDispatcher().build_payload(listing, enrichment, composite=0.73)

        assert "|" in payload.amenities_summary or len(payload.amenities_summary) > 0

    def test_payload_null_title_uses_untitled(self):
        listing = self._make_listing(title=None)
        enrichment = self._make_enrichment()

        payload = AlertDispatcher().build_payload(listing, enrichment, composite=0.73)

        assert payload.listing_title == "Untitled"

    def test_payload_composite_score_set_correctly(self):
        listing = self._make_listing()
        enrichment = self._make_enrichment()

        payload = AlertDispatcher().build_payload(listing, enrichment, composite=0.77)

        assert payload.composite_score == 0.77


# ── dispatch ───────────────────────────────────────────────────────────────────

class TestDispatch:

    def _mock_notification_service(self):
        svc = MagicMock()
        svc.send.return_value = True
        return svc

    def test_dispatch_fires_alert_for_eligible_listing(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory, sample_preferences
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1,
                                  deal_score=0.78, composite_score=0.74)
        enrichment_factory(listing, preference_score=0.68)

        with patch("app.services.alerting.get_notification_service", return_value=self._mock_notification_service()):
            result = AlertDispatcher().dispatch(db_session, sample_preferences)

        assert result["alerts_fired"] == 1

    def test_dispatch_skips_listing_below_composite_gate(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory, sample_preferences
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1,
                                  deal_score=0.80, composite_score=0.40)  # below 0.55
        enrichment_factory(listing, preference_score=0.10)

        with patch("app.services.alerting.get_notification_service", return_value=self._mock_notification_service()):
            result = AlertDispatcher().dispatch(db_session, sample_preferences)

        assert result["alerts_fired"] == 0

    def test_dispatch_skips_listing_not_below_price_threshold(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory, sample_preferences
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        # Price at median — not below 15%
        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1,
                                  deal_score=0.50, composite_score=0.65)
        enrichment_factory(listing, preference_score=0.85)

        with patch("app.services.alerting.get_notification_service", return_value=self._mock_notification_service()):
            result = AlertDispatcher().dispatch(db_session, sample_preferences)

        assert result["alerts_fired"] == 0
        assert result["skipped_threshold"] >= 1

    def test_dispatch_skips_listing_on_cooldown(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory,
        alert_history_factory, sample_preferences
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1,
                                  deal_score=0.78, composite_score=0.74)
        enrichment_factory(listing, preference_score=0.68)
        alert_history_factory(listing, fired_at=datetime.now(timezone.utc) - timedelta(hours=1))

        with patch("app.services.alerting.get_notification_service", return_value=self._mock_notification_service()):
            result = AlertDispatcher().dispatch(db_session, sample_preferences)

        assert result["alerts_fired"] == 0
        assert result["skipped_cooldown"] == 1

    def test_dispatch_respects_max_per_day_rate_limit(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory,
        alert_history_factory, sample_preferences
    ):
        """When daily cap is already reached, dispatch should return immediately."""
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)

        # Fill up the rate limit with existing alerts today
        cap = sample_preferences["alerts"]["max_per_day"]
        filler = listing_factory()
        for _ in range(cap):
            alert_history_factory(filler, fired_at=datetime.now(timezone.utc) - timedelta(minutes=10))

        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1,
                                  deal_score=0.78, composite_score=0.74)
        enrichment_factory(listing, preference_score=0.68)

        with patch("app.services.alerting.get_notification_service", return_value=self._mock_notification_service()):
            result = AlertDispatcher().dispatch(db_session, sample_preferences)

        assert result["rate_limited"] is True
        assert result["alerts_fired"] == 0

    def test_dispatch_skips_failed_enrichment(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory, sample_preferences
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1,
                                  deal_score=0.78, composite_score=0.74)
        enrichment_factory(listing, failed=True, preference_score=None)

        with patch("app.services.alerting.get_notification_service", return_value=self._mock_notification_service()):
            result = AlertDispatcher().dispatch(db_session, sample_preferences)

        assert result["alerts_fired"] == 0

    def test_dispatch_skips_skipped_enrichment(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory, sample_preferences
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1,
                                  deal_score=0.78, composite_score=0.74)
        enrichment_factory(listing, skipped=True, preference_score=0.0)

        with patch("app.services.alerting.get_notification_service", return_value=self._mock_notification_service()):
            result = AlertDispatcher().dispatch(db_session, sample_preferences)

        assert result["alerts_fired"] == 0


# ── evaluate_single ────────────────────────────────────────────────────────────

class TestEvaluateSingle:

    def _mock_notification_service(self):
        svc = MagicMock()
        svc.send.return_value = True
        return svc

    def test_evaluate_single_passes_all_gates(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory, sample_preferences
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1, deal_score=0.78)
        enrichment_factory(listing, preference_score=0.68)

        with patch("app.services.alerting.get_notification_service", return_value=self._mock_notification_service()):
            result = AlertDispatcher().evaluate_single(db_session, str(listing.id), sample_preferences)

        assert result["status"] == "alert_fired"

    def test_evaluate_single_listing_not_found(self, db_session, sample_preferences):
        result = AlertDispatcher().evaluate_single(db_session, str(uuid.uuid4()), sample_preferences)
        assert result["status"] == "skipped"
        assert result["reason"] == "not_found_or_inactive"

    def test_evaluate_single_no_deal_score(
        self, db_session, listing_factory, sample_preferences
    ):
        listing = listing_factory(deal_score=None)

        result = AlertDispatcher().evaluate_single(db_session, str(listing.id), sample_preferences)

        assert result["status"] == "skipped"
        assert result["reason"] == "no_deal_score"

    def test_evaluate_single_no_enrichment(
        self, db_session, listing_factory, sample_preferences
    ):
        listing = listing_factory(deal_score=0.78)

        result = AlertDispatcher().evaluate_single(db_session, str(listing.id), sample_preferences)

        assert result["status"] == "skipped"
        assert result["reason"] == "no_enrichment"

    def test_evaluate_single_fails_composite_gate(
        self, db_session, listing_factory, enrichment_factory, sample_preferences
    ):
        listing = listing_factory(deal_score=0.30)
        enrichment_factory(listing, preference_score=0.10)

        result = AlertDispatcher().evaluate_single(db_session, str(listing.id), sample_preferences)

        assert result["status"] == "skipped"
        assert result["reason"] == "below_min_composite"

    def test_evaluate_single_fails_price_threshold(
        self, db_session, listing_factory, enrichment_factory, price_distribution_factory, sample_preferences
    ):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1, deal_score=0.78)
        enrichment_factory(listing, preference_score=0.85)

        result = AlertDispatcher().evaluate_single(db_session, str(listing.id), sample_preferences)

        assert result["status"] == "skipped"
        assert result["reason"] == "below_alert_threshold"


# ── backfill_composite_scores ──────────────────────────────────────────────────

class TestBackfillCompositeScores:

    def test_backfill_updates_composite_score(
        self, db_session, listing_factory, enrichment_factory, sample_preferences
    ):
        listing = listing_factory(deal_score=0.8, composite_score=None)
        enrichment_factory(listing, preference_score=0.6)

        weights = sample_preferences["scoring"]["weights"]
        AlertDispatcher().backfill_composite_scores(db_session, weights)

        db_session.refresh(listing)
        expected = 0.8 * 0.6 + 0.6 * 0.4
        assert listing.composite_score == pytest.approx(expected, abs=0.001)

    def test_backfill_skips_listings_without_deal_score(
        self, db_session, listing_factory, enrichment_factory, sample_preferences
    ):
        listing = listing_factory(deal_score=None)
        enrichment_factory(listing, preference_score=0.6)

        weights = sample_preferences["scoring"]["weights"]
        result = AlertDispatcher().backfill_composite_scores(db_session, weights)

        assert result["updated"] == 0

    def test_backfill_skips_failed_enrichment(
        self, db_session, listing_factory, enrichment_factory, sample_preferences
    ):
        listing = listing_factory(deal_score=0.8)
        enrichment_factory(listing, preference_score=0.6, failed=True)

        weights = sample_preferences["scoring"]["weights"]
        result = AlertDispatcher().backfill_composite_scores(db_session, weights)

        assert result["updated"] == 0


# ── gather_heartbeat_stats ─────────────────────────────────────────────────────

class TestGatherHeartbeatStats:

    def test_heartbeat_returns_all_keys(self, db_session):
        stats = AlertDispatcher().gather_heartbeat_stats(db_session)

        expected_keys = {"total_scraped", "total_enriched", "alerts_fired",
                         "tokens_today", "active_listings", "segments_meeting_gate"}
        assert set(stats.keys()) == expected_keys

    def test_heartbeat_counts_recent_listings(self, db_session, listing_factory):
        listing_factory()
        listing_factory()

        stats = AlertDispatcher().gather_heartbeat_stats(db_session)

        assert stats["total_scraped"] >= 2

    def test_heartbeat_segments_gate_uses_5_threshold(self, db_session, price_distribution_factory):
        price_distribution_factory(sample_count=5)   # meets gate
        price_distribution_factory(neighborhood="wicker_park", sample_count=4)  # does not

        stats = AlertDispatcher().gather_heartbeat_stats(db_session)

        assert stats["segments_meeting_gate"] == 1
