"""Tests for cross-property dynamic ranges (air fryer temperature/time)."""
import unittest

from mihome_pc.domain import limits


class TestDynamicRanges(unittest.TestCase):
    """Data comes from the user's panel test on a careli.fryer.maf04."""

    MODEL = "careli.fryer.maf04"

    def setUp(self):
        limits.load_limits(reload=True)

    def _range(self, temp):
        return limits.dynamic_range(self.MODEL, "2.3", {"2.4": temp})

    def test_low_temperature_allows_24_hours(self):
        for temp in (40, 60, 85):
            self.assertEqual(self._range(temp), [1, 1440, 1], temp)

    def test_mid_bands(self):
        self.assertEqual(self._range(90), [1, 480, 1])
        self.assertEqual(self._range(105), [1, 480, 1])
        self.assertEqual(self._range(110), [1, 120, 1])
        self.assertEqual(self._range(155), [1, 120, 1])

    def test_high_temperature_one_hour(self):
        for temp in (156, 160, 180, 200):
            self.assertEqual(self._range(temp), [1, 60, 1], temp)

    def test_unknown_model_is_noop(self):
        self.assertIsNone(limits.dynamic_range("xiaomi.heater.ma7", "2.3", {"2.4": 200}))

    def test_unknown_property_is_noop(self):
        self.assertIsNone(limits.dynamic_range(self.MODEL, "2.9", {"2.4": 200}))

    def test_check_value_rejects_over_limit(self):
        rejected = limits.check_value(self.MODEL, "2.3", 600, {"2.4": 180})
        self.assertIsNotNone(rejected)
        limit, why = rejected
        self.assertEqual(limit, 60)
        self.assertIn("60", why)

    def test_check_value_allows_within_limit(self):
        self.assertIsNone(limits.check_value(self.MODEL, "2.3", 30, {"2.4": 180}))
        self.assertIsNone(limits.check_value(self.MODEL, "2.3", 1440, {"2.4": 80}))

    def test_missing_dependency_keeps_spec_range(self):
        self.assertIsNone(limits.dynamic_range(self.MODEL, "2.3", {}))


if __name__ == "__main__":
    unittest.main()
