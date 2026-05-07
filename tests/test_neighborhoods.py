"""Tests for the zip code → neighborhood mapping."""

from app.core.neighborhoods import (
    zip_to_neighborhood,
    get_unmapped_zips,
    reset_unmapped_zips,
)


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


class TestUnmappedZipTracking:
    """Test that unmapped Chicago zip codes are tracked for coverage analysis."""

    def setup_method(self):
        reset_unmapped_zips()

    def test_unmapped_chicago_zip_is_tracked(self):
        zip_to_neighborhood("60606")  # The Loop — not in our mapping
        assert get_unmapped_zips() == {"60606": 1}

    def test_multiple_unmapped_zips_counted(self):
        zip_to_neighborhood("60606")
        zip_to_neighborhood("60606")
        zip_to_neighborhood("60605")
        assert get_unmapped_zips() == {"60606": 2, "60605": 1}

    def test_mapped_zip_not_tracked(self):
        zip_to_neighborhood("60614")  # lincoln_park — mapped
        assert get_unmapped_zips() == {}

    def test_non_chicago_zip_not_tracked(self):
        zip_to_neighborhood("90210")  # Beverly Hills — not Chicago
        assert get_unmapped_zips() == {}

    def test_reset_clears_counts(self):
        zip_to_neighborhood("60606")
        reset_unmapped_zips()
        assert get_unmapped_zips() == {}

    def test_none_and_empty_not_tracked(self):
        zip_to_neighborhood(None)
        zip_to_neighborhood("")
        assert get_unmapped_zips() == {}
