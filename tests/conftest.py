"""Shared test fixtures."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, get_db
from app.main import app
from app.models.alert import AlertHistory
from app.models.enrichment import EnrichmentResult
from app.models.listing import Listing
from app.models.price_distribution import PriceDistribution


@pytest.fixture(scope="session")
def test_engine():
    """Create a test database engine.

    Uses the same Postgres instance but a separate 'apartment_scraper_test' database.
    For unit tests that don't need a real DB, use mocks instead.
    """
    engine = create_engine(
        "postgresql://postgres:postgres@localhost:5432/apartment_scraper_test",
        pool_pre_ping=True,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    """Provide a transactional database session that rolls back after each test."""
    connection = test_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    """FastAPI test client with the DB session overridden."""
    from fastapi.testclient import TestClient

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Factory fixtures — create model instances with sensible defaults
# ---------------------------------------------------------------------------

@pytest.fixture
def listing_factory(db_session):
    """Return a callable that creates and flushes a Listing with overrideable defaults."""
    _counter = [0]

    def _make(**kwargs):
        _counter[0] += 1
        n = _counter[0]
        defaults = dict(
            id=uuid.uuid4(),
            external_id=f"ext_{n}",
            source="craigslist",
            url=f"https://example.com/{n}.html",
            title=f"Test Apartment {n}",
            price=1500,
            bedrooms=1,
            neighborhood="lincoln_park",
            status="active",
        )
        defaults.update(kwargs)
        listing = Listing(**defaults)
        db_session.add(listing)
        db_session.flush()
        return listing

    return _make


@pytest.fixture
def enrichment_factory(db_session):
    """Return a callable that creates and flushes an EnrichmentResult."""
    def _make(listing, **kwargs):
        defaults = dict(
            listing_id=listing.id,
            amenities={"laundry": "in_unit", "dishwasher": True},
            preference_score=0.75,
            llm_notes="Good match overall.",
            tokens_used=300,
            enriched_at=datetime.now(timezone.utc),
            skipped=False,
            failed=False,
        )
        defaults.update(kwargs)
        result = EnrichmentResult(**defaults)
        db_session.add(result)
        db_session.flush()
        return result

    return _make


@pytest.fixture
def price_distribution_factory(db_session):
    """Return a callable that creates and flushes a PriceDistribution."""
    def _make(**kwargs):
        defaults = dict(
            neighborhood="lincoln_park",
            bedroom_count=1,
            median_price=1500.0,
            mad_price=150.0,
            sample_count=10,
            last_updated=datetime.now(timezone.utc),
        )
        defaults.update(kwargs)
        dist = PriceDistribution(**defaults)
        db_session.add(dist)
        db_session.flush()
        return dist

    return _make


@pytest.fixture
def alert_history_factory(db_session):
    """Return a callable that creates and flushes an AlertHistory record."""
    def _make(listing, **kwargs):
        defaults = dict(
            listing_id=listing.id,
            deal_score=0.75,
            preference_score=0.70,
            composite_score=0.73,
            fired_at=datetime.now(timezone.utc),
            delivery_status="sent",
            channel="discord",
        )
        defaults.update(kwargs)
        alert = AlertHistory(**defaults)
        db_session.add(alert)
        db_session.flush()
        return alert

    return _make


@pytest.fixture
def mock_anthropic_client():
    """Return a MagicMock Anthropic client with preset response structure."""
    client = MagicMock()

    def _make_response(text: str, input_tokens: int = 100, output_tokens: int = 50):
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        resp.usage.input_tokens = input_tokens
        resp.usage.output_tokens = output_tokens
        return resp

    client._make_response = _make_response
    return client


@pytest.fixture
def sample_preferences():
    """Return a minimal valid preferences dict matching preferences.yaml structure."""
    return {
        "pricing": {"max_price": 2200, "alert_threshold": 0.15},
        "unit": {"min_bedrooms": 1, "max_bedrooms": 2, "min_sqft": 500},
        "amenities": {"required": ["in_unit_laundry"], "preferred": ["dishwasher"], "dealbreakers": []},
        "fit": {"vibe": "Modern walkable", "building_type": "modern", "transit": []},
        "scoring": {"weights": {"deal": 0.6, "preference": 0.4}, "min_composite_score": 0.55},
        "alerts": {"cooldown_hours": 24, "max_per_day": 100},
        "staleness": {"ttl_days": 14},
    }
