"""Spec parser tests against real MIoT instances.

Fixtures were fetched from
``https://miot-spec.org/miot-spec-v2/instance?type=<urn>`` and are committed
verbatim so the parser is pinned without network access.  They deliberately
cover the four devices the previous round could not drive correctly plus an
air conditioner:

    dishwasher_s2      xiaomi.dishwasher.s2      (had zero availability rules)
    washer_v5          minij.washer.v5           (rules did not match reality)
    health_pot_p1v2    xiaomi.health_pot.p1v2    (too strict)
    fryer_maf04        careli.fryer.maf04        (rules incomplete)
    airconditioner_ma1 xiaomi.aircondition.ma1   (works, ref device)
"""
import glob
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mihome_pc.xiaomi import spec as spec_mod  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "specs")


def spec_files():
    """Only spec instances; *.i18n.json files hold translation tables."""
    return sorted(p for p in glob.glob(os.path.join(FIXTURES, "*.json"))
                  if not p.endswith(".i18n.json"))


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return spec_mod.parse_spec(json.load(handle))


class TestAllFixturesParse(unittest.TestCase):
    def test_every_fixture_parses(self):
        paths = spec_files()
        self.assertGreaterEqual(len(paths), 5)
        for path in paths:
            spec = load(os.path.basename(path))
            self.assertTrue(spec.services, path)
            self.assertTrue(spec.urn.startswith("urn:miot-spec-v2:device:"), path)
            self.assertTrue(spec.readable_properties(), path)

    def test_iids_are_positive(self):
        for path in spec_files():
            spec = load(os.path.basename(path))
            for svc in spec.services:
                self.assertGreater(svc.siid, 0, path)
                for prop in svc.properties:
                    self.assertGreater(prop.piid, 0, path)
                    self.assertEqual(prop.siid, svc.siid)


class TestDishwasher(unittest.TestCase):
    """xiaomi.dishwasher.s2 -- the device with no availability rules at all."""

    def setUp(self):
        self.spec = load("dishwasher_s2.json")

    def test_identity(self):
        self.assertEqual(self.spec.device_type, "dishwasher")
        self.assertEqual(self.spec.model, "xiaomi-s2")

    def test_service_lookup(self):
        svc = self.spec.service("dishwasher")
        self.assertIsNotNone(svc)
        self.assertEqual(svc.siid, 2)
        # same object via siid
        self.assertIs(self.spec.service(2), svc)

    def test_property_lookup(self):
        prop = self.spec.find_property("dishwasher", "mode")
        self.assertIsNotNone(prop)
        self.assertEqual((prop.siid, prop.piid), (2, 4))
        self.assertTrue(prop.writable)
        self.assertEqual(prop.widget, "enum")

    def test_on_property_is_the_power_switch(self):
        power = self.spec.power_property()
        self.assertIsNotNone(power)
        self.assertEqual(power.name, "on")
        self.assertEqual(power.format, "bool")

    def test_status_has_chinese_labels(self):
        status = self.spec.status_property()
        self.assertIsNotNone(status)
        self.assertEqual(status.format, "uint8")
        # value 2 = busy; the fixture's comment is Chinese
        label = status.label_for(2)
        self.assertTrue(label)
        self.assertNotEqual(label, "2")
        self.assertEqual(len(status.value_list), len({o.value for o in status.value_list}))

    def test_actions_present(self):
        svc = self.spec.service("dishwasher")
        names = [a.name for a in svc.actions]
        self.assertIn("start-wash", names)
        self.assertIn("stop-washing", names)
        for action in svc.actions:
            self.assertEqual(action.siid, 2)
            self.assertGreater(action.aiid, 0)

    def test_events_present(self):
        svc = self.spec.service("dishwasher")
        self.assertIn("wash-end", [e.name for e in svc.events])

    def test_missing_lookup_returns_none(self):
        self.assertIsNone(self.spec.service("no-such-service"))
        self.assertIsNone(self.spec.property(99, 99))


class TestAirConditioner(unittest.TestCase):
    def setUp(self):
        self.spec = load("airconditioner_ma1.json")

    def test_numeric_range_and_clamp(self):
        target = self.spec.find_property("air-conditioner", "target-temperature")
        self.assertIsNotNone(target, "AC should expose target-temperature")
        self.assertIsNotNone(target.value_range)
        low, high, step = target.value_range
        self.assertLess(low, high)
        self.assertEqual(target.clamp(low - 50), low)
        self.assertEqual(target.clamp(high + 50), high)
        if step:
            clamped = target.clamp(low + step * 2.4)
            self.assertEqual((clamped - low) % step, 0)

    def test_accepts(self):
        target = self.spec.find_property("air-conditioner", "target-temperature")
        low, high = target.value_range[0], target.value_range[1]
        self.assertTrue(target.accepts((low + high) // 2))
        self.assertFalse(target.accepts(high + 1000))


class TestCoercion(unittest.TestCase):
    def setUp(self):
        self.spec = load("dishwasher_s2.json")

    def test_bool_coercion(self):
        prop = self.spec.power_property()
        self.assertIs(prop.coerce(1), True)
        self.assertIs(prop.coerce(0), False)
        self.assertIs(prop.coerce("true"), True)
        self.assertIs(prop.coerce("off"), False)
        self.assertIs(prop.coerce(False), False)

    def test_int_coercion(self):
        mode = self.spec.find_property("dishwasher", "mode")
        self.assertEqual(mode.coerce("3"), 3)
        self.assertEqual(mode.coerce(3.0), 3)

    def test_label_for_unknown_value(self):
        mode = self.spec.find_property("dishwasher", "mode")
        self.assertEqual(mode.label_for(9999), "9999")


class TestSerialization(unittest.TestCase):
    def test_roundtrip_to_dict(self):
        spec = load("washer_v5.json")
        payload = spec.to_dict()
        rebuilt = spec_mod.parse_spec(payload)
        self.assertEqual(rebuilt.urn, spec.urn)
        self.assertEqual([s.siid for s in rebuilt.services],
                         [s.siid for s in spec.services])
        self.assertEqual(len(rebuilt.writable_properties()),
                         len(spec.writable_properties()))

    def test_json_serializable(self):
        spec = load("health_pot_p1v2.json")
        text = json.dumps(spec.to_dict())
        self.assertIn("urn:miot-spec-v2:device:health-pot", text)


class TestUrnHelpers(unittest.TestCase):
    def test_urn_of(self):
        # urn:<spec>:<kind>:<name>:<code>:<vendor>:<version>
        urn = "urn:miot-spec-v2:property:left-time:0000003C:xiaomi-s2:1"
        self.assertEqual(spec_mod.urn_of(urn, 2), "property")
        self.assertEqual(spec_mod.urn_of(urn, 3), "left-time")
        self.assertEqual(spec_mod.urn_of(urn, 5), "xiaomi-s2")
        self.assertEqual(spec_mod.urn_of("", 2), "")

    def test_short_name(self):
        self.assertEqual(
            spec_mod.short_name("urn:miot-spec-v2:property:left-time:0000003C:xiaomi-s2:1"),
            "left-time")

    def test_widget_table_covers_common_formats(self):
        for fmt in ("bool", "uint8", "uint16", "int32", "float", "string"):
            self.assertIn(fmt, spec_mod.WIDGET_BY_FORMAT)


if __name__ == "__main__":
    unittest.main()
