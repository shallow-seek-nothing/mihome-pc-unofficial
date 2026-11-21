"""The regression sweep: it must report, never crash."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import regression  # noqa: E402


class TestChecks(unittest.TestCase):
    def test_every_check_has_a_path_and_callable(self):
        self.assertTrue(regression.CHECKS)
        for name, path, checker in regression.CHECKS:
            self.assertTrue(name)
            self.assertTrue(path.startswith("/api/"))
            self.assertTrue(callable(checker))

    def test_paths_are_unique(self):
        paths = [path for _n, path, _c in regression.CHECKS]
        self.assertEqual(len(paths), len(set(paths)))


class TestRun(unittest.TestCase):
    def test_unreachable_server_is_reported_not_raised(self):
        rows = regression.run("http://127.0.0.1:9")
        self.assertTrue(rows)
        self.assertFalse(any(ok for _n, ok, _d in rows))
        for _name, _ok, detail in rows:
            self.assertTrue(detail)


if __name__ == "__main__":
    unittest.main()
