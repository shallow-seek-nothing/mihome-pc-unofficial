"""Release checker: the parsers plus a whole synthetic tree."""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import check_release  # noqa: E402


class TestParsers(unittest.TestCase):
    def test_package_version(self):
        self.assertEqual(check_release.package_version('VERSION = "1.2.3"\n'),
                         "1.2.3")
        self.assertEqual(check_release.package_version(""), "")

    def test_readme_version(self):
        text = "> \u5f53\u524d\u7248\u672c **9.9.9**\u3002\u53d8\u66f4\u660e\u7ec6\u89c1"
        self.assertEqual(check_release.readme_version(text), "9.9.9")
        self.assertEqual(check_release.readme_version("nothing"), "")

    def test_changelog_takes_the_top_entry(self):
        text = "# log\n\n## 1.0.0 - x\n\n## 0.9.0 - y\n"
        self.assertEqual(check_release.changelog_version(text), "1.0.0")

    def test_hashes_are_extracted(self):
        text = "| a | 1 KB | `%s` |" % ("a" * 64)
        self.assertEqual(check_release.hashes_in(text), {"a" * 64})


class TestCheck(unittest.TestCase):
    def build_tree(self, folder, version="1.2.3", note=True, artifacts=True):
        os.makedirs(os.path.join(folder, "mihome_pc"))
        os.makedirs(os.path.join(folder, "docs"))
        os.makedirs(os.path.join(folder, "dist"))
        with open(os.path.join(folder, "mihome_pc", "__init__.py"), "w",
                  encoding="utf-8") as handle:
            handle.write('VERSION = "%s"\n' % version)
        with open(os.path.join(folder, "README.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("> \u5f53\u524d\u7248\u672c **%s**\u3002" % version)
        with open(os.path.join(folder, "CHANGELOG.md"), "w",
                  encoding="utf-8") as handle:
            handle.write("## %s - today\n" % version)
        with open(os.path.join(folder, "docs", "04-\u529f\u80fd\u5bf9\u7167.md"),
                  "w", encoding="utf-8") as handle:
            handle.write("- \u7248\u672c\uff1a%s\n" % version)
        if artifacts:
            for pattern in check_release.ARTIFACTS:
                with open(os.path.join(folder, "dist", pattern % version), "wb") as h:
                    h.write(b"payload")
        if note and artifacts:
            lines = []
            for pattern in check_release.ARTIFACTS:
                path = os.path.join(folder, "dist", pattern % version)
                lines.append("`%s`" % check_release.digest(path))
            with open(check_release.release_note_path(
                    os.path.join(folder, "dist"), version), "w",
                    encoding="utf-8") as handle:
                handle.write("\n".join(lines))

    def test_clean_tree_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            self.build_tree(folder)
            rows = check_release.check(folder)
        self.assertTrue(all(ok for _name, ok, _detail in rows), rows)

    def test_version_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            self.build_tree(folder)
            with open(os.path.join(folder, "README.md"), "w",
                      encoding="utf-8") as handle:
                handle.write("> \u5f53\u524d\u7248\u672c **0.0.1**\u3002")
            rows = check_release.check(folder)
        self.assertFalse({n: ok for n, ok, _d in rows}["README \u7248\u672c"])

    def test_missing_artifact_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            self.build_tree(folder, artifacts=False)
            rows = check_release.check(folder)
        self.assertFalse({n: ok for n, ok, _d in rows}["\u56db\u4ef6\u4ea7\u7269"])

    def test_stale_hash_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            self.build_tree(folder)
            path = os.path.join(folder, "dist",
                                check_release.ARTIFACTS[0] % "1.2.3")
            with open(path, "wb") as handle:
                handle.write(b"changed after the note was written")
            rows = check_release.check(folder)
        self.assertFalse({n: ok for n, ok, _d in rows}["sha256 \u4e00\u81f4"])


if __name__ == "__main__":
    unittest.main()
