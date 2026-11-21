"""Pinned devices: list ops plus how they survive a restart."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import favorites  # noqa: E402
from mihome_pc.web import server as web_server  # noqa: E402


class TestToggle(unittest.TestCase):
    def test_pin_then_unpin(self):
        dids, pinned = favorites.toggle([], "a")
        self.assertEqual(dids, ["a"])
        self.assertTrue(pinned)
        dids, pinned = favorites.toggle(dids, "a")
        self.assertEqual(dids, [])
        self.assertFalse(pinned)

    def test_keeps_existing_order(self):
        dids, _ = favorites.toggle(["a", "b"], "c")
        self.assertEqual(dids, ["a", "b", "c"])

    def test_prune_drops_missing_devices(self):
        self.assertEqual(favorites.prune(["a", "b", "z"], ["a", "b"]),
                         ["a", "b"])


class TestOrder(unittest.TestCase):
    def test_pinned_first_keeping_inner_order(self):
        devices = [{"did": "b"}, {"did": "a"}, {"did": "c"}]
        ordered = favorites.order(devices, ["c"])
        self.assertEqual([item["did"] for item in ordered], ["c", "b", "a"])

    def test_pinned_order_wins(self):
        devices = [{"did": "a"}, {"did": "b"}]
        ordered = favorites.order(devices, ["b", "a"])
        self.assertEqual([item["did"] for item in ordered], ["b", "a"])

    def test_no_pins_is_unchanged(self):
        devices = [{"did": "a"}, {"did": "b"}]
        self.assertEqual(favorites.order(devices, []), devices)


class TestProviderPersistence(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.original = web_server._data_dir
        web_server._data_dir = lambda: self.folder.name
        self.addCleanup(setattr, web_server, "_data_dir", self.original)
        self.provider = web_server.DemoProvider()

    def test_toggle_writes_and_reads_the_file(self):
        result = self.provider.toggle_favorite("demo-washer")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["pinned"])
        self.assertTrue(os.path.exists(
            os.path.join(self.folder.name, "favorites.json")))
        self.assertEqual(self.provider.favorites()["dids"], ["demo-washer"])

        again = self.provider.toggle_favorite("demo-washer")
        self.assertFalse(again["pinned"])
        self.assertEqual(self.provider.favorites()["dids"], [])

    def test_device_list_is_sorted_and_flagged(self):
        washers = [d for d in self.provider.devices()
                   if d["did"] == "demo-washer"]
        self.assertTrue(washers, "demo data must contain the washer")
        self.provider.toggle_favorite("demo-washer")
        devices = self.provider.devices()
        self.assertEqual(devices[0]["did"], "demo-washer")
        self.assertTrue(devices[0]["pinned"])
        self.assertFalse(any(d["pinned"] for d in devices[1:]))


if __name__ == "__main__":
    unittest.main()
