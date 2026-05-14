"""Tests for the deduplication logic."""

from app.workers.insert_helpers import compute_dedup_hash, normalize_address


class TestNormalizeAddress:
    def test_abbreviation_expansion(self):
        assert "street" in normalize_address("123 N State St")
        assert "avenue" in normalize_address("456 W Michigan Ave")
        assert "boulevard" in normalize_address("789 S Lakeshore Blvd")

    def test_case_insensitive(self):
        assert normalize_address("123 N STATE ST") == normalize_address("123 n state st")

    def test_strips_apt_unit(self):
        result = normalize_address("123 N State St Apt 4")
        assert "apt" not in result

    def test_strips_the_prefix(self):
        result = normalize_address("The Lakeview Tower")
        assert not result.startswith("the")

    def test_empty_and_none(self):
        assert normalize_address(None) == ""
        assert normalize_address("") == ""

    def test_strips_punctuation(self):
        result = normalize_address("123 N. State St., Chicago")
        assert "." not in result


class TestComputeDedupHash:
    def test_same_address_same_hash(self):
        h1 = compute_dedup_hash("123 N State St", "4A", 2)
        h2 = compute_dedup_hash("123 N State St", "4A", 2)
        assert h1 == h2

    def test_different_formatting_same_hash(self):
        h1 = compute_dedup_hash("123 N State St", "4A", 2)
        h2 = compute_dedup_hash("123 N State Street", "4a", 2)
        assert h1 == h2

    def test_different_address_different_hash(self):
        h1 = compute_dedup_hash("123 N State St", "4A", 2)
        h2 = compute_dedup_hash("456 W Michigan Ave", "4A", 2)
        assert h1 != h2

    def test_different_unit_different_hash(self):
        h1 = compute_dedup_hash("123 N State St", "4A", 2)
        h2 = compute_dedup_hash("123 N State St", "5B", 2)
        assert h1 != h2

    def test_different_bedrooms_different_hash(self):
        h1 = compute_dedup_hash("123 N State St", "4A", 1)
        h2 = compute_dedup_hash("123 N State St", "4A", 2)
        assert h1 != h2

    def test_none_values(self):
        h = compute_dedup_hash(None, None, None)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_hash_length(self):
        h = compute_dedup_hash("123 N State St", "4A", 2)
        assert len(h) == 16
