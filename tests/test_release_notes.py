"""Release notes: the sidecar file that ships next to the artifacts."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import release_notes  # noqa: E402


class TestHuman(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(release_notes._human(512), "512 B")
        self.assertEqual(release_notes._human(2048), "2.0 KB")
        self.assertEqual(release_notes._human(9 * 1024 * 1024), "9.0 MB")


class TestCollect(unittest.TestCase):
    def test_only_this_version_is_picked(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("mihome-pc-1.2.3-portable-win64.zip",
                         "mihome-pc_1.2.3_all.deb",
                         "mihome-pc-1.2.2-portable-win64.zip",
                         "notes.txt"):
                with open(os.path.join(folder, name), "wb") as handle:
                    handle.write(b"x" * 10)
            entries = release_notes.collect(folder, "1.2.3")
        names = [name for name, _, _ in entries]
        self.assertEqual(names, ["mihome-pc-1.2.3-portable-win64.zip",
                                 "mihome-pc_1.2.3_all.deb"])
        self.assertTrue(all(len(sha) == 64 for _, _, sha in entries))

    def test_missing_directory_is_empty(self):
        self.assertEqual(release_notes.collect("/nope/nowhere", "1"), [])


class TestRender(unittest.TestCase):
    def render(self, entries=None):
        entries = entries if entries is not None else [
            ("mihome-pc-9.9.9-portable-win64.zip", 9 * 1024 * 1024, "a" * 64),
            ("mihome-pc_9.9.9_all.deb", 600 * 1024, "b" * 64),
        ]
        return release_notes.render("9.9.9", entries)

    def test_lists_every_artifact_with_hash(self):
        text = self.render()
        self.assertIn("mihome-pc-9.9.9-portable-win64.zip", text)
        self.assertIn("a" * 64, text)
        self.assertIn("9.0 MB", text)

    def test_install_steps_use_the_version(self):
        text = self.render()
        self.assertIn("mihome-pc_9.9.9_all.deb", text)
        self.assertIn("mihome-pc-9.9.9-linux.tar.gz", text)
        self.assertNotIn("%s", text, "no unformatted placeholders")

    def test_limits_are_listed(self):
        text = self.render()
        for item in release_notes.KNOWN_LIMITS:
            self.assertIn(item, text)

    def test_empty_artifacts_says_so(self):
        text = self.render(entries=[])
        self.assertIn("\u5148\u8dd1 build_win.py", text)


if __name__ == "__main__":
    unittest.main()
