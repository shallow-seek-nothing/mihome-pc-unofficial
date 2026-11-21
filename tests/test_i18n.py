"""i18n table tests: the multiLanguage payload turns English labels Chinese."""
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.xiaomi import spec as spec_mod  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "specs")
CJK = re.compile("[\u4e00-\u9fff]")


def load_json(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


class TestSynthesisedEnum(unittest.TestCase):
    """The health pot has no spec value-list but i18n enumerates its modes.

    Real case (xiaomi.health_pot.p1v2, 2026-11-19): property 2.3 "device mode"
    is a bare uint8 in the spec, while zh_cn carries
    ``service:002:property:003:valuelist:000`` = 烧水 and friends.  Without this
    synthesis the UI showed a meaningless number stepper.
    """

    def _spec_without_enum(self):
        import copy
        payload = load_json("health_pot_p1v2.json")
        payload = copy.deepcopy(payload)
        for service in payload.get("services") or ():
            for prop in service.get("properties") or ():
                prop.pop("value-list", None)
        return payload

    def test_enum_built_from_translations(self):
        payload = self._spec_without_enum()
        spec = spec_mod.parse_spec(payload)
        target = None
        for service in spec.services:
            for prop in service.properties:
                if service.siid == 2 and prop.piid == 3:
                    target = prop
        self.assertIsNotNone(target)
        self.assertFalse(target.value_list, "fixture should have no enum")

        table = load_json("health_pot_p1v2.i18n.json")["data"]
        spec_mod.apply_i18n(spec, table)
        self.assertTrue(target.value_list, "enum should be synthesised")
        labels = [option.label for option in target.value_list]
        self.assertTrue(all(labels), labels)
        self.assertIn(0, [option.value for option in target.value_list])

    def test_control_becomes_a_select(self):
        from mihome_pc.domain import model as device_model
        payload = self._spec_without_enum()
        spec = spec_mod.parse_spec(payload)
        spec_mod.apply_i18n(spec, load_json("health_pot_p1v2.i18n.json")["data"])
        view = device_model.DeviceView("d", "xiaomi.health_pot.p1v2", "", True, spec)
        control = view.control(2, 3)
        self.assertIsNotNone(control)
        self.assertIn(control.widget, ("select", "buttons"))
        self.assertTrue(control.options)


class TestI18n(unittest.TestCase):
    def setUp(self):
        self.spec = spec_mod.parse_spec(load_json("airconditioner_ma1.json"))
        self.table = load_json("airconditioner_ma1.i18n.json")["data"]

    def test_spec_starts_english(self):
        service = self.spec.service(2)
        self.assertFalse(CJK.search(service.label), service.label)

    def test_chinese_labels_applied(self):
        spec_mod.apply_i18n(self.spec, self.table)
        service = self.spec.service(2)
        self.assertTrue(CJK.search(service.label), service.label)
        mode = service.property(2)
        self.assertTrue(CJK.search(mode.label), mode.label)

    def test_value_list_labels_follow_index_not_value(self):
        """``valuelist:000`` is the first entry, whatever its enum value is."""
        before = self.spec.service(2).property(2)
        self.assertEqual([o.value for o in before.value_list], [2, 3, 4, 5])
        spec_mod.apply_i18n(self.spec, self.table)
        after = self.spec.service(2).property(2)
        self.assertEqual([o.value for o in after.value_list], [2, 3, 4, 5])
        for option in after.value_list:
            self.assertTrue(CJK.search(option.label), option.label)

    def test_language_preference(self):
        self.assertIsNotNone(spec_mod.pick_language(self.table))
        self.assertIsNotNone(spec_mod.pick_language(self.table, ("en",)))
        self.assertIsNone(spec_mod.pick_language(self.table, ("de",)))

    def test_english_request_keeps_english(self):
        spec_mod.apply_i18n(self.spec, self.table, preference=("en",))
        service = self.spec.service(2)
        self.assertFalse(CJK.search(service.label), service.label)

    def test_empty_table_is_a_no_op(self):
        before = self.spec.service(2).label
        spec_mod.apply_i18n(self.spec, {})
        self.assertEqual(self.spec.service(2).label, before)

    def test_control_layer_picks_up_labels(self):
        from mihome_pc.domain import model as device_model
        spec_mod.apply_i18n(self.spec, self.table)
        view = device_model.DeviceView("d", "xiaomi.aircondition.ma1", "", True, self.spec)
        labels = [c.label for c in view.controls]
        self.assertTrue(any(CJK.search(label or "") for label in labels), labels)
        self.assertFalse(any(label == "Mode" for label in labels if label))


if __name__ == "__main__":
    unittest.main()
