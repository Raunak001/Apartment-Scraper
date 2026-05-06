"""Tests for configuration and preferences loading."""

from app.core.config import load_preferences, PREFERENCES_PATH


class TestLoadPreferences:
    def test_preferences_file_exists(self):
        assert PREFERENCES_PATH.exists(), "preferences.yaml must exist at project root"

    def test_load_returns_dict(self):
        prefs = load_preferences()
        assert isinstance(prefs, dict)

    def test_required_top_level_keys(self):
        prefs = load_preferences()
        expected_keys = {"search", "pricing", "unit", "amenities", "fit", "scoring", "alerts"}
        assert expected_keys.issubset(prefs.keys())

    def test_scoring_weights_present(self):
        prefs = load_preferences()
        weights = prefs["scoring"]["weights"]
        assert "deal" in weights
        assert "preference" in weights

    def test_scoring_weights_sum_to_one(self):
        prefs = load_preferences()
        weights = prefs["scoring"]["weights"]
        assert abs(weights["deal"] + weights["preference"] - 1.0) < 0.001

    def test_neighborhoods_not_empty(self):
        prefs = load_preferences()
        neighborhoods = prefs["search"]["neighborhoods"]
        assert len(neighborhoods) > 0

    def test_max_price_is_positive(self):
        prefs = load_preferences()
        assert prefs["pricing"]["max_price"] > 0
