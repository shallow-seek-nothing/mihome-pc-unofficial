"""Desktop entry: menu model, icon bytes, single-instance lock."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc import app, tray  # noqa: E402


class TestMenu(unittest.TestCase):
    def test_entries(self):
        items = tray.build_menu("http://127.0.0.1:1234/", "0.6.0")
        ids = [item[0] for item in items]
        self.assertIn(tray.ID_OPEN, ids)
        self.assertIn(tray.ID_QUIT, ids)
        self.assertIn(-1, ids)                      # separator
        labels = [item[1] for item in items]
        self.assertTrue(any("0.6.0" in label for label in labels))
        self.assertIn("打开 PC 控制端", labels)
        self.assertNotIn("打开米家 PC", labels)
        actions = {item[0]: item[2] for item in items if item[0] > 0}
        self.assertTrue(actions[tray.ID_OPEN])
        self.assertTrue(actions[tray.ID_QUIT])

    def test_disabled_rows_are_not_clickable(self):
        items = tray.build_menu("http://x/", "")
        self.assertFalse(any(enabled for item_id, _label, enabled in items
                             if item_id == 0))


class TestIcon(unittest.TestCase):
    def _png(self, width):
        import struct
        header = b"\x89PNG\r\n\x1a\n"
        ihdr = struct.pack(">II", width, width) + b"\x08\x06\x00\x00\x00"
        return header + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00" * 8

    def test_header_and_entry(self):
        png = self._png(512)
        data = tray.ico_from_png(png)
        self.assertEqual(data[:6], b"\x00\x00\x01\x00\x01\x00")
        self.assertEqual(len(data), 22 + len(png))
        self.assertEqual(data[6], 0)                 # 0 means 256+
        self.assertEqual(data[12:14], b"\x20\x00")     # bits per pixel = 32
        self.assertEqual(data[18:22], b"\x16\x00\x00\x00")  # data offset 22

    def test_small_png_keeps_its_size(self):
        data = tray.ico_from_png(self._png(32))
        self.assertEqual(data[6], 32)
        self.assertEqual(data[7], 32)


class TestSingleInstance(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self._original = app._data_dir
        app._data_dir = lambda: self.folder.name
        self.addCleanup(self._restore)

    def _restore(self):
        app._data_dir = self._original

    def test_lock_roundtrip(self):
        app._write_lock("http://127.0.0.1:9/")
        info = app.read_lock()
        self.assertEqual(info["url"], "http://127.0.0.1:9/")
        self.assertEqual(info["pid"], os.getpid())
        app._drop_lock()
        self.assertEqual(app.read_lock(), {})

    def test_dead_instance_is_not_reused(self):
        app._write_lock("http://127.0.0.1:9/")
        self.assertEqual(app.running_instance(), "")

    def test_no_lock_means_no_instance(self):
        self.assertEqual(app.read_lock(), {})
        self.assertEqual(app.running_instance(), "")

    def test_responds_rejects_garbage(self):
        self.assertFalse(app.responds(""))
        self.assertFalse(app.responds("http://127.0.0.1:9/"))


class TestTrayShim(unittest.TestCase):
    """The PowerShell fallback that actually ships on Windows."""

    def test_script_ships_with_the_package(self):
        from mihome_pc import tray_ps
        self.assertTrue(os.path.isfile(tray_ps._script_path()))

    def test_command_carries_every_argument(self):
        from mihome_pc import tray_ps
        captured = {}

        class FakeProcess:
            def wait(self):
                return 0

            def terminate(self):
                pass

        def fake_popen(command, **kwargs):
            captured['command'] = command
            captured['kwargs'] = kwargs
            return FakeProcess()

        original_popen = tray_ps.subprocess.Popen
        original_shell = tray_ps._powershell
        tray_ps.subprocess.Popen = fake_popen
        tray_ps._powershell = lambda: "powershell.exe"
        try:
            code = tray_ps.run("http://127.0.0.1:9/", 4321, icon="i.ico",
                               folder="d", ready_file="r.ready")
        finally:
            tray_ps.subprocess.Popen = original_popen
            tray_ps._powershell = original_shell
        self.assertEqual(code, 0)
        command = captured["command"]
        for expected in ("-File", "http://127.0.0.1:9/", "4321", "i.ico",
                         "d", "r.ready", "-WindowStyle"):
            self.assertIn(expected, command)


class TestTrayAvailability(unittest.TestCase):
    def test_probe_is_false_off_windows(self):
        original = sys.platform
        sys.platform = "linux-test"
        try:
            self.assertFalse(tray.probe())
        finally:
            sys.platform = original

    def test_available_matches_platform(self):
        self.assertEqual(tray.available(), sys.platform.startswith("win"))

    def test_unavailable_platform_raises(self):
        original = sys.platform
        sys.platform = "linux-test"
        try:
            with self.assertRaises(OSError):
                tray.Tray("title", "")
        finally:
            sys.platform = original


if __name__ == "__main__":
    unittest.main()
