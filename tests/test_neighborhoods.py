"""Tests for the zip code → neighborhood mapping."""

from app.core.neighborhoods import zip_to_neighborhood


class TestZipToNeighborhood:
    def test_lincoln_park(self):
        assert zip_to_neighborhood("60614") == "lincoln_park"

    def test_wicker_park(self):
        assert zip_to_neighborhood("60622") == "wicker_park"

    def test_logan_square(self):
        assert zip_to_neighborhood("60647") == "logan_square"

    def test_old_town(self):
        assert zip_to_neighborhood("60610") == "old_town"

    def test_west_loop(self):
        assert zip_to_neighborhood("60607") == "west_loop"

    def test_west_loop_alt_zip(self):
        assert zip_to_neighborhood("60661") == "west_loop"

    def test_west_town(self):
        assert zip_to_neighborhood("60612") == "west_town"

    def test_river_north(self):
        assert zip_to_neighborhood("60654") == "river_north"

    def test_river_north_alt_zip(self):
        assert zip_to_neighborhood("60611") == "river_north"

    def test_nine_digit_zip(self):
        assert zip_to_neighborhood("60614-1234") == "lincoln_park"

    def test_unknown_zip_returns_none(self):
        assert zip_to_neighborhood("90210") is None

    def test_none_input(self):
        assert zip_to_neighborhood(None) is None

    def test_empty_string(self):
        assert zip_to_neighborhood("") is None

    def test_whitespace_handling(self):
        assert zip_to_neighborhood("  60614  ") == "lincoln_park"
