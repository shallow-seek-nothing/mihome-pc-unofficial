"""Release-boundary tests using only small synthetic files and archives."""
import io
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import build_deb  # noqa: E402
import distribution_policy  # noqa: E402
import verify_distribution  # noqa: E402


LICENSE = b"PolyForm Noncommercial License 1.0.0\n\nRequired Notice: Copyright test\n"
MIT = b"MIT License\n"
NOTICE = b"Third-party boundaries\n"


class TestDistributionPolicy(unittest.TestCase):
    def test_restricted_names_cover_catalog_apk_and_reverse_engineering(self):
        names = [
            "mihome_pc/assets/catalog.json",
            "samples/official.APK",
            "mihome_pc/assets/resources.arsc",
            "reverse/jadx/source/Example.java",
            "mihome_pc/smali/Example.smali",
        ]
        self.assertEqual(distribution_policy.restricted_members(names),
                         sorted(names))

    def test_code_original_logo_and_licenses_are_allowed(self):
        names = [
            "mihome_pc/web/server.py",
            "mihome_pc/static/logo.png",
            "LICENSE.txt",
            "LICENSES/MIT.txt",
            "THIRD_PARTY_NOTICES.md",
        ]
        self.assertEqual(distribution_policy.restricted_members(names), [])

    def test_copy_project_package_fails_before_copying_restricted_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "mihome_pc", "assets")
            os.makedirs(source)
            with open(os.path.join(source, "catalog.json"), "wb") as handle:
                handle.write(b"restricted")
            target = os.path.join(folder, "output")
            with self.assertRaises(SystemExit):
                distribution_policy.copy_project_package(folder, target)
            self.assertFalse(os.path.exists(target))


class TestDistributionVerifier(unittest.TestCase):
    version = "1.2.3"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dist = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _legal_files(prefix):
        return {
            prefix + "LICENSE.txt": LICENSE,
            prefix + "THIRD_PARTY_NOTICES.md": NOTICE,
            prefix + "LICENSES/MIT.txt": MIT,
        }

    def _write_zip(self, path, missing_legal=False, restricted=False):
        files = self._legal_files("mihome-pc/")
        files["mihome-pc/python38/LICENSE.txt"] = b"CPython license\n"
        files["mihome-pc/mihome_pc/__init__.py"] = b"VERSION = '1.2.3'\n"
        if missing_legal:
            del files["mihome-pc/LICENSES/MIT.txt"]
        if restricted:
            files["mihome-pc/mihome_pc/assets/catalog.json"] = b"restricted"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)

    @staticmethod
    def _tar_bytes(files):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, data in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return output.getvalue()

    def _write_tarball(self, path, missing_legal=False, restricted=False):
        files = self._legal_files("")
        files["lib/mihome_pc/cli.py"] = b"# package\n"
        if missing_legal:
            del files["LICENSES/MIT.txt"]
        if restricted:
            files["lib/mihome_pc/assets/official.apk"] = b"restricted"
        with open(path, "wb") as handle:
            handle.write(self._tar_bytes(files))

    def _write_deb(self, path, missing_legal=False, restricted=False):
        files = self._legal_files("usr/share/doc/mihome-pc/")
        files["usr/lib/mihome-pc/mihome_pc/cli.py"] = b"# package\n"
        if missing_legal:
            del files["usr/share/doc/mihome-pc/LICENSES/MIT.txt"]
        if restricted:
            files["usr/lib/mihome-pc/mihome_pc/jadx/Example.java"] = b"restricted"
        build_deb.write_ar(path, [
            ("debian-binary", b"2.0\n"),
            ("control.tar.gz", self._tar_bytes({"./control": b"Package: test\n"})),
            ("data.tar.gz", self._tar_bytes(files)),
        ])

    def _build_all(self, missing_legal=False, restricted=False):
        paths = {}
        for pattern, kind in verify_distribution.ARTIFACTS:
            path = os.path.join(self.dist, pattern % self.version)
            if kind in ("win64", "win32"):
                self._write_zip(path, missing_legal, restricted)
            elif kind == "deb":
                self._write_deb(path, missing_legal, restricted)
            else:
                self._write_tarball(path, missing_legal, restricted)
            paths[kind] = path
        return paths

    def test_all_four_synthetic_artifacts_pass(self):
        self._build_all()
        self.assertEqual(len(verify_distribution.verify_all(
            self.dist, self.version)), 4)

    def test_every_archive_kind_fails_when_historical_mit_is_missing(self):
        paths = self._build_all(missing_legal=True)
        for kind, path in paths.items():
            with self.subTest(kind=kind), self.assertRaisesRegex(
                    ValueError, "LICENSES/MIT.txt"):
                verify_distribution.verify_archive(path, kind)

    def test_every_archive_kind_fails_on_restricted_content(self):
        paths = self._build_all(restricted=True)
        for kind, path in paths.items():
            with self.subTest(kind=kind), self.assertRaisesRegex(
                    ValueError, "restricted content"):
                verify_distribution.verify_archive(path, kind)


if __name__ == "__main__":
    unittest.main()
