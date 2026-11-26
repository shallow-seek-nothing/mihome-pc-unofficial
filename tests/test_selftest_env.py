"""Self-check helpers: the environment report behind `selftest --env`."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc import cli  # noqa: E402


def status_of(rows, name):
    for label, status, _detail in rows:
        if label == name:
            return status
    return None


class TestEnvironmentReport(unittest.TestCase):
    def test_empty_data_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = cli.environment_report(data_path=folder)
        self.assertEqual(status_of(rows, "\u6570\u636e\u76ee\u5f55"), "ok")
        self.assertEqual(status_of(rows, "\u51ed\u636e"), "off")
        self.assertEqual(status_of(rows, "spec \u7f13\u5b58"), "off")
        self.assertEqual(status_of(rows, "\u63d2\u4ef6\u6587\u6848"), "off")

    def test_populated_data_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "credentials.json"), "w",
                      encoding="utf-8") as handle:
                handle.write("{}")
            specs = os.path.join(folder, "specs")
            os.makedirs(specs)
            with open(os.path.join(specs, "a.json"), "w", encoding="utf-8") as h:
                h.write("{}")
            plugins = os.path.join(folder, "plugins", "minij.washer.v5")
            os.makedirs(plugins)
            with open(os.path.join(plugins, "texts.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"a": "b"}, handle)
            rows = cli.environment_report(data_path=folder)
            self.assertEqual(status_of(rows, "\u51ed\u636e"), "ok")
            self.assertEqual(status_of(rows, "spec \u7f13\u5b58"), "ok")
            self.assertEqual(status_of(rows, "\u63d2\u4ef6\u6587\u6848"), "ok")
            details = {name: detail for name, _s, detail in rows}
            self.assertIn("1 ", details["spec \u7f13\u5b58"])

    def test_reports_one_row_per_topic(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = cli.environment_report(data_path=folder)
        names = [name for name, _s, _d in rows]
        self.assertEqual(sorted(names), sorted(["\u6570\u636e\u76ee\u5f55",
                                                "\u51ed\u636e",
                                                "spec \u7f13\u5b58",
                                                "\u63d2\u4ef6\u6587\u6848",
                                                "\u65e5\u5fd7",
                                                "\u65e5\u5fd7\u7ea7\u522b",
                                                "\u5d29\u6e83\u8bb0\u5f55"]))

    def test_log_rows_point_at_the_data_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = cli.environment_report(data_path=folder)
        details = {name: detail for name, _s, detail in rows}
        self.assertIn(folder, details["\u65e5\u5fd7"])
        self.assertEqual(status_of(rows, "\u5d29\u6e83\u8bb0\u5f55"), "ok")

    def test_crash_files_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            logs_dir = os.path.join(folder, "logs")
            os.makedirs(logs_dir)
            with open(os.path.join(logs_dir, "crash-20260101-000000.log"), "w",
                      encoding="utf-8") as handle:
                handle.write("boom\n")
            rows = cli.environment_report(data_path=folder)
        self.assertEqual(status_of(rows, "\u5d29\u6e83\u8bb0\u5f55"), "warn")
        details = {name: detail for name, _s, detail in rows}
        self.assertIn("crash-20260101-000000.log", details["\u5d29\u6e83\u8bb0\u5f55"])


if __name__ == "__main__":
    unittest.main()
