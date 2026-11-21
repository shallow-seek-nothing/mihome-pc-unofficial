"""型号目录/卡片布局：只从用户数据目录加载，缺失时平稳降级。

这两份数据来自安卓客户端内置配置，不随仓库和安装包发布，
所以这里用**合成的小型目录**验证加载与降级逻辑，不依赖被剔除的 APK 数据。
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mihome_pc.domain import model as device_model  # noqa: E402

SYNTHETIC = {
    "minij.washer.v5": {"n": "洗衣机", "c": "清洁电器", "ds": "洗衣机"},
}


class CatalogCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._catalog_dirs = device_model._catalog_dirs
        # 把“用户数据目录”指到临时目录，并把内置 assets 从查找链里去掉
        self.catalog_root = os.path.join(self.tmp.name, "catalog")
        device_model._catalog_dirs = lambda: [self.catalog_root]
        device_model._CATALOG = None
        device_model._LAYOUTS = None

    def tearDown(self):
        device_model._catalog_dirs = self._catalog_dirs
        device_model._CATALOG = None
        device_model._LAYOUTS = None
        self.tmp.cleanup()

    def write(self, name, payload):
        os.makedirs(self.catalog_root, exist_ok=True)
        with open(os.path.join(self.catalog_root, name), "w",
                  encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)

    def test_missing_catalog_degrades_to_empty(self):
        self.assertEqual(device_model.load_catalog_entry("minij.washer.v5"), {})
        self.assertEqual(device_model.load_card_layout("minij.washer.v5"), {})

    def test_local_catalog_is_loaded(self):
        self.write("catalog.json", SYNTHETIC)
        entry = device_model.load_catalog_entry("minij.washer.v5")
        self.assertEqual(entry["n"], "洗衣机")

    def test_local_card_layout_is_loaded(self):
        self.write("card_layouts.json", {"minij.washer.v5": {"cards": {}}})
        self.assertIn("cards",
                      device_model.load_card_layout("minij.washer.v5"))

    def test_broken_json_does_not_break_the_ui(self):
        os.makedirs(self.catalog_root, exist_ok=True)
        with open(os.path.join(self.catalog_root, "catalog.json"), "w",
                  encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(device_model.load_catalog_entry("minij.washer.v5"), {})

    def test_display_model_never_shows_a_full_urn(self):
        self.assertEqual(device_model.display_model("minij.washer.v5"),
                         "minij.washer.v5")
        urn = "urn:miot-spec-v2:device:washer:0000A01F:minij:1"
        self.assertEqual(device_model.display_model(urn), "washer")
        self.assertEqual(device_model.display_model(""), "")


if __name__ == "__main__":
    unittest.main()
