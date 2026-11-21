"""Plugin wording: matching, fallbacks, and the fault-code table."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.domain import model, plugin_text  # noqa: E402
from mihome_pc.xiaomi import spec as spec_mod  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "specs")

TEXTS = {
    "mode_DrumClean": "\u7b52\u81ea\u6d01",
    "mode_DailyWash": "\u65e5\u5e38\u6d17",
    "mode_Dry": "\u5355\u70d8\u5e72",
    "mode_DryAirWash": "\u7a7a\u6c14\u6d17",
    "spin_speed_no_spin": "\u514d\u8131\u6c34",
    "water_level_high": "\u9ad8\u6c34\u4f4d",
    "detergent_normal": "\u666e\u901a\u6d17\u6da4\u5242",
    "temperature_sensor_problem": "\u6e29\u5ea6\u4f20\u611f\u5668\u5f02\u5e38\uff08tF\uff09",
    "temperature_sensor_problem_method": "1\u3001\u68c0\u67e5 NTC \u63d2\u7ebf",
    "spin_failed": "\u4e0d\u5e73\u8861\u62a5\u8b66\uff08UF\uff09",
    "spin_failed_method": "\u91cd\u65b0\u6446\u653e\u8863\u7269",
    # a generic message with a ``_failed`` key but no fault code: must be ignored
    "load_failed": "\u52a0\u8f7d\u5931\u8d25\uff0c\u70b9\u51fb\u91cd\u8bd5",
}


class TestValueLabel(unittest.TestCase):
    def test_name_match(self):
        self.assertEqual(plugin_text.value_label(TEXTS, "mode", "Drum Clean"),
                         "\u7b52\u81ea\u6d01")

    def test_multi_word_name_match(self):
        self.assertEqual(plugin_text.value_label(TEXTS, "spin-speed", "no spin"),
                         "\u514d\u8131\u6c34")

    def test_short_label_does_not_pick_a_foreign_key(self):
        # "normal" must not take ``detergent_normal`` when the property is
        # drying-level: a wrong label is worse than a fallback
        self.assertIsNone(plugin_text.value_label(TEXTS, "drying-level", "normal"))

    def test_unknown_value_is_none(self):
        self.assertIsNone(plugin_text.value_label(TEXTS, "mode", "Wool"))
        self.assertIsNone(plugin_text.value_label({}, "mode", "Drum Clean"))

    def test_head_rule_keeps_similar_keys_apart(self):
        texts = {
            "water_level_middle": "\u4e2d\u6c34\u4f4d",
            "dry_level_middle": "\u5373\u7a7f",
            "water_low": "\u5230\u8fbe\u8865\u6c34\u6b21\u6570\uff0c\u6c34\u4f4d\u4ecd\u8fc7\u4f4e\uff08LF\uff09",
            "water_level_low": "\u4f4e\u6c34\u4f4d",
        }
        # value name alone would pick ``dry_level_middle`` (即穿) for water level
        self.assertEqual(
            plugin_text.value_label(texts, "target-water-level", "middle"),
            "\u4e2d\u6c34\u4f4d")
        # and the shorter ``water_low`` is a fault blurb, not a level
        self.assertEqual(
            plugin_text.value_label(texts, "target-water-level", "low"),
            "\u4f4e\u6c34\u4f4d")
        self.assertEqual(plugin_text.value_label(texts, "drying-level", "middle"),
                         "\u5373\u7a7f")

    def test_alias_bridges_temp_and_temperature(self):
        texts = {"temp_cold": "\u5e38\u6e29"}
        self.assertEqual(plugin_text.value_label(texts, "target-temperature", "cold"),
                         "\u5e38\u6e29")


class TestLoad(unittest.TestCase):
    def test_missing_plugin_is_empty(self):
        self.assertEqual(plugin_text.load("nope.model", tempfile.gettempdir()), {})
        self.assertEqual(plugin_text.load("", tempfile.gettempdir()), {})

    def test_reads_texts_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "demo.model")
            os.makedirs(path)
            with open(os.path.join(path, "texts.json"), "w", encoding="utf-8") as fh:
                json.dump(TEXTS, fh, ensure_ascii=False)
            self.assertEqual(plugin_text.load("demo.model", folder)["mode_Dry"],
                             "\u5355\u70d8\u5e72")


class TestFaultNotes(unittest.TestCase):
    def test_pairs_title_with_method_and_extracts_code(self):
        notes = {note["code"]: note for note in plugin_text.fault_notes(TEXTS)}
        self.assertIn("tF", notes)
        self.assertIn("UF", notes)
        self.assertEqual(notes["UF"]["method"], "\u91cd\u65b0\u6446\u653e\u8863\u7269")

    def test_no_texts_no_notes(self):
        self.assertEqual(plugin_text.fault_notes({}), [])

    def test_generic_failed_message_is_not_a_fault(self):
        codes = [note["code"] for note in plugin_text.fault_notes(TEXTS)]
        self.assertNotIn("", codes)
        self.assertEqual(len(codes), 2)


class TestControlLabels(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(FIXTURES, "washer_v5.json"), encoding="utf-8") as handle:
            self.spec = spec_mod.parse_spec(json.load(handle))

    def mode_control(self, texts=None):
        view = model.DeviceView("did1", "minij.washer.v5", "\u6d17\u8863\u673a", True,
                                self.spec, texts=texts)
        return [c for c in view.controls if c.name == "mode"][0]

    def test_plugin_wording_wins(self):
        labels = dict(self.mode_control(TEXTS).options)
        self.assertEqual(labels[14], "\u7b52\u81ea\u6d01")

    def test_falls_back_without_a_plugin(self):
        labels = dict(self.mode_control().options)
        # Boiling: the dictionary says 煮洗, the plugin says 高温洗
        self.assertEqual(labels[12], "\u716e\u6d17")
        self.assertTrue(labels[1])             # still a label, just our own

    def test_plugin_refines_the_dictionary(self):
        texts = {"mode_Boiling": "\u9ad8\u6e29\u6d17"}
        labels = dict(self.mode_control(texts).options)
        self.assertEqual(labels[12], "\u9ad8\u6e29\u6d17")

    def test_unknown_values_keep_the_fallback(self):
        labels = dict(self.mode_control({"mode_DrumClean": "\u7b52\u81ea\u6d01"}).options)
        self.assertEqual(labels[14], "\u7b52\u81ea\u6d01")
        self.assertTrue(labels[1])             # Daily Wash still labelled


if __name__ == "__main__":
    unittest.main()
