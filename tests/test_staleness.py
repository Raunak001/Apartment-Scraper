"""Tests for staleness detection and last_checked_at lifecycle."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.listing import Listing
from app.models.preference import Preference
from app.workers.insert_helpers import batch_insert_listings
from app.scrapers.base import RawListing


class TestMarkStaleListings:
    """Test the mark_stale_listings Celery task."""

    def test_old_active_listing_becomes_stale(self, db_session):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        listing = Listing(
            id=uuid.uuid4(),
            external_id="stale1",
            source="craigslist",
            url="https://example.com/stale1.html",
            price=1500,
            status="active",
            last_checked_at=old,
            scraped_at=old,
        )
        db_session.add(listing)
        db_session.flush()

        pref = Preference(
            id=uuid.uuid4(),
            config={"staleness": {"ttl_days": 14}},
            active=True,
        )
        db_session.add(pref)
        db_session.flush()

        with patch("app.workers.staleness_tasks.SessionLocal", return_value=db_session):
            with patch.object(db_session, "close"):
                from app.workers.staleness_tasks import mark_stale_listings
                result = mark_stale_listings.apply().result

        assert result["marked_stale"] == 1
        db_session.refresh(listing)
        assert listing.status == "stale"

    def test_recent_listing_stays_active(self, db_session):
        recent = datetime.now(timezone.utc) - timedelta(days=3)
        listing = Listing(
            id=uuid.uuid4(),
            external_id="fresh1",
            source="craigslist",
            url="https://example.com/fresh1.html",
            price=1500,
            status="active",
            last_checked_at=recent,
            scraped_at=recent,
        )
        db_session.add(listing)
        db_session.flush()

        pref = Preference(
            id=uuid.uuid4(),
            config={"staleness": {"ttl_days": 14}},
            active=True,
        )
        db_session.add(pref)
        db_session.flush()

        with patch("app.workers.staleness_tasks.SessionLocal", return_value=db_session):
            with patch.object(db_session, "close"):
                from app.workers.staleness_tasks import mark_stale_listings
                result = mark_stale_listings.apply().result

        assert result["marked_stale"] == 0
        db_session.refresh(listing)
        assert listing.status == "active"

    def test_duplicate_status_not_touched(self, db_session):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        listing = Listing(
            id=uuid.uuid4(),
            external_id="dup1",
            source="craigslist",
            url="https://example.com/dup1.html",
            price=1500,
            status="dedup_duplicate",
            last_checked_at=old,
            scraped_at=old,
        )
        db_session.add(listing)
        db_session.flush()

        pref = Preference(
            id=uuid.uuid4(),
            config={"staleness": {"ttl_days": 14}},
            active=True,
        )
        db_session.add(pref)
        db_session.flush()

        with patch("app.workers.staleness_tasks.SessionLocal", return_value=db_session):
            with patch.object(db_session, "close"):
                from app.workers.staleness_tasks import mark_stale_listings
                result = mark_stale_listings.apply().result

        assert result["marked_stale"] == 0
        db_session.refresh(listing)
        assert listing.status == "dedup_duplicate"

    def test_uses_scraped_at_when_last_checked_is_null(self, db_session):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        listing = Listing(
            id=uuid.uuid4(),
            external_id="null_check1",
            source="craigslist",
            url="https://example.com/null_check1.html",
            price=1500,
            status="active",
            last_checked_at=None,
            scraped_at=old,
        )
        db_session.add(listing)
        db_session.flush()

        pref = Preference(
            id=uuid.uuid4(),
            config={"staleness": {"ttl_days": 14}},
            active=True,
        )
        db_session.add(pref)
        db_session.flush()

        with patch("app.workers.staleness_tasks.SessionLocal", return_value=db_session):
            with patch.object(db_session, "close"):
                from app.workers.staleness_tasks import mark_stale_listings
                result = mark_stale_listings.apply().result

        assert result["marked_stale"] == 1
        db_session.refresh(listing)
        assert listing.status == "stale"


class TestLastCheckedAtUpdate:
    """Test that batch_insert_listings updates last_checked_at on re-seen listings."""

    def test_duplicate_updates_last_checked_at(self, db_session):
        listing = Listing(
            id=uuid.uuid4(),
            external_id="reseen1",
            source="craigslist",
            url="https://example.com/reseen1.html",
            price=1500,
            status="active",
            last_checked_at=datetime.now(timezone.utc) - timedelta(days=7),
        )
        db_session.add(listing)
        db_session.commit()

        raw = RawListing(
            external_id="reseen1",
            source="craigslist",
            url="https://example.com/reseen1.html",
            title="Same listing",
            price=1500,
        )
        before = listing.last_checked_at
        batch_insert_listings([raw], db_session)
        db_session.refresh(listing)
        assert listing.last_checked_at > before

    def test_stale_listing_reactivated_on_reseen(self, db_session):
        listing = Listing(
            id=uuid.uuid4(),
            external_id="stale_reseen1",
            source="craigslist",
            url="https://example.com/stale_reseen1.html",
            price=1500,
            status="stale",
            last_checked_at=datetime.now(timezone.utc) - timedelta(days=20),
        )
        db_session.add(listing)
        db_session.commit()

        raw = RawListing(
            external_id="stale_reseen1",
            source="craigslist",
            url="https://example.com/stale_reseen1.html",
            title="Reactivated listing",
            price=1500,
        )
        result = batch_insert_listings([raw], db_session)
        db_session.refresh(listing)
        assert listing.status == "active"
        assert result["reactivated"] == 1
