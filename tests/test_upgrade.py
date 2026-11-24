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

    def test_branding_renames_shortcuts_and_removes_legacy_entries(self):
        module = runpy.run_path(os.path.join(ROOT, "packaging", "build_win.py"))
        script = module["INSTALL_PS1"]
        self.assertIn("'PC 控制端.lnk'", script)
        self.assertIn("'卸载 PC 控制端.lnk'", script)
        self.assertIn("$lnk.Description = 'PC 控制端'", script)
        self.assertIn("'米家 PC.lnk'", script)
        self.assertIn("Remove-Item -Force $legacyDesktopLnk", script)
        self.assertIn("Remove-Item -Recurse -Force $legacyStartFolder", script)

    def test_linux_desktop_entry_uses_original_pc_controller_branding(self):
        import tempfile
        module = runpy.run_path(os.path.join(ROOT, "packaging", "build_deb.py"))
        self.assertIn("Name=PC Controller", module["DESKTOP"])
        self.assertIn("Name[zh_CN]=PC 控制端", module["DESKTOP"])
        self.assertNotIn("Name=Mi Home", module["DESKTOP"])
        with tempfile.TemporaryDirectory() as folder:
            module["stage_tree"].__globals__["STAGE"] = folder
            module["stage_tree"]()
            icon = os.path.join(folder, "usr", "share", "icons", "hicolor",
                                "256x256", "apps", "mihome-pc.png")
            self.assertTrue(os.path.isfile(icon))
            with open(os.path.join(ROOT, "mihome_pc", "static", "logo.png"),
                      "rb") as source, open(icon, "rb") as packaged:
                self.assertEqual(packaged.read(), source.read())
            docs = os.path.join(folder, "usr", "share", "doc", "mihome-pc")
            for relative in ("LICENSE.txt", "THIRD_PARTY_NOTICES.md",
                             os.path.join("LICENSES", "MIT.txt")):
                self.assertTrue(os.path.isfile(os.path.join(docs, relative)))
            with open(os.path.join(docs, "copyright"), encoding="utf-8") as handle:
                self.assertIn("License: PolyForm-Noncommercial-1.0.0", handle.read())

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
