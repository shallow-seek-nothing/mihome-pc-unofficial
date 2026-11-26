"""``app.lock`` 与 ``mihome-pc stop``。

Linux 没有托盘，`stop` 是唯一能从一个没有终端的进程里退出来的入口，
所以这几条要钉死：锁能读回、损坏的锁不炸、别人的锁不能删、
过期锁会被清理、没有锁时明确报失败（而不是假装成功）。
"""
import os
import tempfile
import unittest

from mihome_pc import app, lockfile

DEAD_URL = "http://127.0.0.1:9/"          # 端口 9（discard）通常没人监听


class TestLockfile(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.dir = self.folder.name

    def test_roundtrip(self):
        info = lockfile.write(DEAD_URL, self.dir)
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(lockfile.read(self.dir)["url"], DEAD_URL)
        self.assertTrue(lockfile.drop(self.dir))
        self.assertEqual(lockfile.read(self.dir), {})

    def test_corrupt_lock_reads_empty(self):
        path = os.path.join(self.dir, lockfile.LOCK_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(lockfile.read(self.dir), {})

    def test_json_that_is_not_an_object_reads_empty(self):
        path = os.path.join(self.dir, lockfile.LOCK_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[1, 2]")
        self.assertEqual(lockfile.read(self.dir), {})

    def test_own_only_drop_keeps_a_foreign_lock(self):
        lockfile.write(DEAD_URL, self.dir, pid=999999)
        self.assertFalse(lockfile.drop(self.dir, own=True))
        self.assertEqual(lockfile.read(self.dir)["pid"], 999999)
        self.assertTrue(lockfile.drop(self.dir))

    def test_no_temp_file_left_behind(self):
        lockfile.write(DEAD_URL, self.dir)
        leftovers = [name for name in os.listdir(self.dir) if name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestStop(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        original = app._data_dir
        app._data_dir = lambda: self.folder.name
        self.addCleanup(lambda: setattr(app, "_data_dir", original))

    def test_stop_without_lock_reports_failure(self):
        # 返回 1 而不是 0：没东西可停不能算成功，脚本里要看退出码
        self.assertEqual(app.stop(), 1)

    def test_stop_clears_a_stale_lock(self):
        app._write_lock(DEAD_URL)
        self.assertEqual(app.stop(wait_seconds=0.5), 0)
        self.assertEqual(app.read_lock(), {})


if __name__ == "__main__":
    unittest.main()
