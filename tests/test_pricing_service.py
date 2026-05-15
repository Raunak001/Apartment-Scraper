"""Tests for PricingService — price distribution rebuilds and deal scoring."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from app.services.pricing import MAD_FLOOR, MIN_SAMPLE_COUNT, PricingService


class TestScoreListing:
    """Tests for PricingService.score_listing()."""

    def test_score_at_median_is_approximately_half(self, db_session, listing_factory, price_distribution_factory):
        dist = price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                          median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is not None
        assert score == pytest.approx(0.5, abs=0.01)

    def test_score_below_median_is_above_half(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1200, neighborhood="lincoln_park", bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is not None
        assert score > 0.5

    def test_score_above_median_is_below_half(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1800, neighborhood="lincoln_park", bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is not None
        assert score < 0.5

    def test_score_returns_none_missing_price(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=None, neighborhood="lincoln_park", bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is None

    def test_score_returns_none_missing_neighborhood(self, db_session, listing_factory):
        listing = listing_factory(price=1500, neighborhood=None, bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is None

    def test_score_returns_none_missing_bedrooms(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=None)

        score = PricingService().score_listing(db_session, listing)

        assert score is None

    def test_score_returns_none_no_distribution(self, db_session, listing_factory):
        listing = listing_factory(price=1500, neighborhood="wicker_park", bedrooms=2)

        score = PricingService().score_listing(db_session, listing)

        assert score is None

    def test_score_returns_none_insufficient_samples(self, db_session, listing_factory, price_distribution_factory):
        """Segments with fewer than MIN_SAMPLE_COUNT samples should not produce a score."""
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0,
                                   sample_count=MIN_SAMPLE_COUNT - 1)
        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is None

    def test_score_returns_none_at_min_sample_count_boundary(self, db_session, listing_factory, price_distribution_factory):
        """Exactly MIN_SAMPLE_COUNT samples should produce a score."""
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0,
                                   sample_count=MIN_SAMPLE_COUNT)
        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is not None

    def test_score_returns_none_when_mad_below_floor(self, db_session, listing_factory, price_distribution_factory):
        """Near-zero MAD (all prices identical) should not produce a score."""
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=MAD_FLOOR - 0.5,
                                   sample_count=10)
        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is None

    def test_score_is_bounded_between_zero_and_one(self, db_session, listing_factory, price_distribution_factory):
        """Extreme prices should still produce scores in [0, 1]."""
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)

        for price in [100, 10000]:
            listing = listing_factory(price=price, neighborhood="lincoln_park", bedrooms=1)
            score = PricingService().score_listing(db_session, listing)
            assert score is not None
            assert 0.0 <= score <= 1.0

    def test_score_is_rounded_to_4_decimal_places(self, db_session, listing_factory, price_distribution_factory):
        price_distribution_factory(neighborhood="lincoln_park", bedroom_count=1,
                                   median_price=1500.0, mad_price=150.0, sample_count=10)
        listing = listing_factory(price=1337, neighborhood="lincoln_park", bedrooms=1)

        score = PricingService().score_listing(db_session, listing)

        assert score is not None
        assert score == round(score, 4)


class TestRebuildDistributions:
    """Tests for PricingService.rebuild_distributions()."""

    def test_rebuild_empty_db_returns_zero_segments(self, db_session):
        result = PricingService().rebuild_distributions(db_session)

        assert result["segments_updated"] == 0
        assert result["total_sampled"] == 0

    def test_rebuild_groups_by_neighborhood_and_bedroom(self, db_session, listing_factory):
        listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1, status="active")
        listing_factory(price=1600, neighborhood="lincoln_park", bedrooms=1, status="active")
        listing_factory(price=2000, neighborhood="wicker_park", bedrooms=2, status="active")

        result = PricingService().rebuild_distributions(db_session)

        assert result["segments_updated"] == 2
        assert result["total_sampled"] == 3

    def test_rebuild_computes_correct_median(self, db_session, listing_factory):
        prices = [1400, 1500, 1600]
        for p in prices:
            listing_factory(price=p, neighborhood="lincoln_park", bedrooms=1, status="active")

        PricingService().rebuild_distributions(db_session)

        from app.models.price_distribution import PriceDistribution
        dist = db_session.query(PriceDistribution).filter_by(
            neighborhood="lincoln_park", bedroom_count=1
        ).first()

        assert dist is not None
        assert float(dist.median_price) == pytest.approx(1500.0)

    def test_rebuild_computes_correct_mad(self, db_session, listing_factory):
        """MAD of [1400, 1500, 1600] from median 1500 is median([100, 0, 100]) = 100."""
        prices = [1400, 1500, 1600]
        for p in prices:
            listing_factory(price=p, neighborhood="lincoln_park", bedrooms=1, status="active")

        PricingService().rebuild_distributions(db_session)

        from app.models.price_distribution import PriceDistribution
        dist = db_session.query(PriceDistribution).filter_by(
            neighborhood="lincoln_park", bedroom_count=1
        ).first()

        assert float(dist.mad_price) == pytest.approx(100.0)

    def test_rebuild_excludes_listings_outside_60day_window(self, db_session, listing_factory):
        old_time = datetime.now(timezone.utc) - timedelta(days=65)

        listing = listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1, status="active")
        # Force-set scraped_at to be outside the window
        from app.models.listing import Listing
        db_session.query(Listing).filter(Listing.id == listing.id).update({"scraped_at": old_time})
        db_session.flush()

        result = PricingService().rebuild_distributions(db_session)

        assert result["total_sampled"] == 0

    def test_rebuild_excludes_stale_listings(self, db_session, listing_factory):
        listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1, status="stale")

        result = PricingService().rebuild_distributions(db_session)

        assert result["total_sampled"] == 0

    def test_rebuild_excludes_listings_without_price(self, db_session, listing_factory):
        listing_factory(price=None, neighborhood="lincoln_park", bedrooms=1, status="active")

        result = PricingService().rebuild_distributions(db_session)

        assert result["total_sampled"] == 0

    def test_rebuild_upserts_on_second_call(self, db_session, listing_factory):
        listing_factory(price=1500, neighborhood="lincoln_park", bedrooms=1, status="active")
        PricingService().rebuild_distributions(db_session)

        # Add new listing — should update the same segment
        listing_factory(price=1600, neighborhood="lincoln_park", bedrooms=1, status="active")
        result = PricingService().rebuild_distributions(db_session)

        assert result["segments_updated"] == 1  # same segment, upserted
        assert result["total_sampled"] == 2
