"""Tests for the FastAPI endpoints.

These tests require a running test database (see conftest.py).
"""

import uuid
from datetime import datetime, timezone

from app.models.listing import Listing


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["database"] == "connected"

    def test_health_has_timestamp(self, client):
        response = client.get("/health")
        data = response.json()
        assert "timestamp" in data


class TestStatusEndpoint:
    def test_status_empty_db(self, client):
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert data["total_listings"] == 0
        assert data["by_source"] == {}

    def test_status_with_listings(self, client, db_session):
        # Insert a test listing
        listing = Listing(
            id=uuid.uuid4(),
            external_id="test123",
            source="craigslist",
            url="https://example.com/test123.html",
            title="Test Apartment",
            price=1500,
            neighborhood="lincoln_park",
            bedrooms=2,
        )
        db_session.add(listing)
        db_session.flush()

        response = client.get("/status")
        data = response.json()
        assert data["total_listings"] == 1
        assert data["by_source"]["craigslist"] == 1
        assert data["by_neighborhood"]["lincoln_park"] == 1


class TestListingsEndpoint:
    def test_listings_empty(self, client):
        response = client.get("/listings")
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_listings_with_data(self, client, db_session):
        listing = Listing(
            id=uuid.uuid4(),
            external_id="test456",
            source="craigslist",
            url="https://example.com/test456.html",
            title="Cozy 1BR in Wicker Park",
            price=1200,
            neighborhood="wicker_park",
            bedrooms=1,
            status="active",
        )
        db_session.add(listing)
        db_session.flush()

        response = client.get("/listings")
        data = response.json()
        assert data["count"] == 1
        assert data["listings"][0]["price"] == 1200
        assert data["listings"][0]["neighborhood"] == "wicker_park"

    def test_listings_filter_by_neighborhood(self, client, db_session):
        for i, hood in enumerate(["lincoln_park", "wicker_park"]):
            listing = Listing(
                id=uuid.uuid4(),
                external_id=f"filter_test_{i}",
                source="craigslist",
                url=f"https://example.com/filter{i}.html",
                price=1500,
                neighborhood=hood,
                bedrooms=1,
                status="active",
            )
            db_session.add(listing)
        db_session.flush()

        response = client.get("/listings?neighborhood=lincoln_park")
        data = response.json()
        assert data["count"] == 1
        assert data["listings"][0]["neighborhood"] == "lincoln_park"

    def test_listings_filter_by_max_price(self, client, db_session):
        for price in [1000, 2000, 3000]:
            listing = Listing(
                id=uuid.uuid4(),
                external_id=f"price_test_{price}",
                source="craigslist",
                url=f"https://example.com/price{price}.html",
                price=price,
                bedrooms=1,
                status="active",
            )
            db_session.add(listing)
        db_session.flush()

        response = client.get("/listings?max_price=1500")
        data = response.json()
        assert data["count"] == 1
        assert data["listings"][0]["price"] == 1000
