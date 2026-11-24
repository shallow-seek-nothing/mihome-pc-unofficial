"""First-upgrade safeguards and the post-scan loading experience."""
import os
import runpy
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import release_notes  # noqa: E402


class TestWindowsUpgrade(unittest.TestCase):
    def test_x64_and_x86_installers_stage_and_roll_back(self):
        scripts = []
        for name in ("build_win.py", "build_win32.py"):
            module = runpy.run_path(os.path.join(ROOT, "packaging", name))
            scripts.append(module["INSTALL_PS1"])
        self.assertEqual(scripts[0], scripts[1])
        script = scripts[0]
        self.assertLess(script.index("Copy-Item -Path (Join-Path $here '*')"),
                        script.index("Rename-WithRetry $dest"))
        self.assertIn("$staging = $dest + '.new-'", script)
        self.assertIn("$backup = $dest + '.old-'", script)
        self.assertIn("Copy-Item -Path $oldData -Destination $dest", script)
        self.assertIn("Rename-WithRetry $backup", script)
        self.assertIn("Where-Object { $_.Path -like ($dest + '*') }", script)
        self.assertNotIn("检测到已安装，先删除旧版本", script)

    def test_release_notes_explain_both_upgrade_paths(self):
        text = release_notes.render("0.7.37", [])
        self.assertIn("## 从旧版升级", text)
        self.assertIn("安装到桌面.cmd", text)
        self.assertIn("无需先卸载", text)
        self.assertIn("整个 `data` 文件夹", text)
        self.assertIn("失败会恢复旧版", text)


class TestLoginLoading(unittest.TestCase):
    def test_loading_waits_for_devices_and_exposes_retry(self):
        with open(os.path.join(ROOT, "mihome_pc", "static", "app.js"),
                  encoding="utf-8") as handle:
            script = handle.read()
        with open(os.path.join(ROOT, "mihome_pc", "static", "index.html"),
                  encoding="utf-8") as handle:
            page = handle.read()
        self.assertIn("永远相信美好的事情即将发生", script)
        self.assertIn("账号已连接，正在加载设备列表…", script)
        self.assertIn("if (result && result.devicesError)", script)
        self.assertIn("showLoginError(result.devicesError.message", script)
        self.assertIn('$("login-retry").addEventListener("click"', script)
        self.assertIn('id="login-loading"', page)
        self.assertIn('id="login-retry"', page)
        self.assertIn('aria-live="polite"', page)


if __name__ == "__main__":
    unittest.main()
